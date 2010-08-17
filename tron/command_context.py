"""Command Context is how we construct the command line for a command which may have variables that need to be rendered.

"""

class CommandContext(object):
    def __init__(self, objects = None):
        """Initialize
        
        Args
          objects - List of objects we'll use to do lookups
        """
        self.object_list = objects or []

    def add(self, item):
        self.object_list.append(item)
        
    def __getitem__(self, name):
        for obj in object_list:
            # First we try to access the object like a dictionary
            try:
                return obj[name]
            except (AttributeError, KeyError):
                pass
            
            # Dictionary didn't work, check for an attribute
            try:
                return getattr(obj, name)
            except AttributeError:
                continue
        else:
            raise KeyError(name)