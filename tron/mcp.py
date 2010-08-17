import logging
import weakref
import yaml
import os
import sys
import subprocess

from tron import job
from twisted.internet import reactor
from tron.utils import timeutils

SECS_PER_DAY = 86400
MICRO_SEC = .000001
log = logging.getLogger('tron.mcp')
STATE_FILE = 'tron_state.yaml'
STATE_SLEEP = 3

def sleep_time(run_time):
    sleep = run_time - timeutils.current_time()
    seconds = sleep.days * SECS_PER_DAY + sleep.seconds + sleep.microseconds * MICRO_SEC
    return max(0, seconds)


class Error(Exception): pass


class StateHandler(object):
    def __init__(self, mcp, working_dir, writing=False):
        self.mcp = mcp
        self.working_dir = working_dir
        self.write_pid = None
        self.writing_enabled = writing

    def restore_job(self, job, data):
        job.running = data['running']
        for r_data in reversed(data['runs']):
            run = job.restore_run(r_data)
            if run.is_scheduled:
                reactor.callLater(sleep_time(run.run_time), self.mcp.run_job, run)

        next = job.next_to_finish()
        if job.running:
            if not next:
                self.mcp.schedule_next_run(job)
            elif next.is_queued:
                next.start()

    def store_data(self):
        """Stores the state of tron"""
        # If tron is already storing data, don't start again till it's done
        if not self.writing_enabled or (self.write_pid and not os.waitpid(self.write_pid, os.WNOHANG)[0]):
            return 

        file_path = os.path.join(self.working_dir, STATE_FILE)
        log.info("Storing state in %s", file_path)
        
        pid = os.fork()
        if pid:
            self.write_pid = pid
        else:
            file = open(file_path, 'w')
            yaml.dump(self.data, file, default_flow_style=False, indent=4)
            file.close()
            os._exit(os.EX_OK)
        
        reactor.callLater(STATE_SLEEP, self.store_data)

    def get_state_file_path(self):
        return os.path.join(self.working_dir, STATE_FILE)

    def load_data(self):
        log.info('Restoring state from %s', self.get_state_file_path())
        
        data_file = open(self.get_state_file_path())
        data = yaml.load(data_file)
        data_file.close()

        return data
    
    @property
    def data(self):
        data = {}
        for j in self.mcp.jobs.itervalues():
            data[j.name] = j.data
        return data

class MasterControlProgram(object):
    """master of tron's domain
    
    This object is responsible for figuring who needs to run and when. It will be the main entry point
    where our daemon finds work to do
    """
    def __init__(self, working_dir, context=None, state_handler=None):
        self.jobs = {}
        self.nodes = []
        self.state_handler = state_handler or StateHandler(self, working_dir)
        self.context = context or context.ChainedCommandContext()

    def add_nodes(self, node_pool):
        if not node_pool:
            return

        for node in node_pool.nodes:
            if not node in self.nodes:
                self.nodes.append(node)

    def add_job_nodes(self, job):
        self.add_nodes(job.node_pool)
        for action in job.topo_actions:
            self.add_nodes(action.node_pool)

    def setup_job_dir(self, job):
        job.output_dir = os.path.join(self.state_handler.working_dir, job.name)
        if not os.path.exists(job.output_dir):
            os.mkdir(job.output_dir)

    def add_job(self, tron_job):
        if tron_job.name in self.jobs:
            self.jobs[tron_job.name].disable()
            tron_job.absorb_old_job(self.jobs[tron_job.name])

        self.jobs[tron_job.name] = tron_job
        self.add_job_nodes(tron_job)
        self.setup_job_dir(tron_job)

    def _schedule(self, run):
        sleep = sleep_time(run.run_time)
        if sleep == 0:
            run.set_run_time(timeutils.current_time())
        reactor.callLater(sleep, self.run_job, run)

    def schedule_next_run(self, job):
        next = job.next_run()
        if not next is None:
            log.info("Scheduling next job for %s", next.job.name)
            self._schedule(next)

    def run_job(self, now):
        """This runs when a job was scheduled.
        Here we run the job and schedule the next time it should run
        """
        if not now.job.running:
            return
        
        self.schedule_next_run(now.job)
        if not (now.is_running or now.is_failed or now.is_success):
            log.debug("Running next scheduled job")
            now.scheduled_start()

    def enable_job(self, job):
        if not job.runs[0].is_scheduled:
            self.schedule_next_run(job)
        job.enable()

    def disable_job(self, job):
        job.disable()

    def disable_all(self):
        for jo in self.jobs.itervalues():
            self.disable_job(jo)

    def enable_all(self):
        for jo in self.jobs.itervalues():
            self.enable_job(jo)
    
    def run_jobs(self):
        """This schedules the first time each job runs"""
        data = None
        if os.path.isfile(self.state_handler.get_state_file_path()):
            data = self.state_handler.load_data()

        for tron_job in self.jobs.itervalues():
            if data and tron_job.name in data:
                self.state_handler.restore_job(tron_job, data[tron_job.name])
            else:
                self.schedule_next_run(tron_job)
        
        self.state_handler.writing_enabled = True
        self.state_handler.store_data()

