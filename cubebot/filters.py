import config
from telegram.ext import BaseFilter
from enum import Enum, auto
from model import session, Player
from functools import wraps

class GameStates(Enum):
    INIT = auto()
    PLAY = auto()
    END = auto()
    
class UserType(Enum):
    ADMIN = auto()
    PLAYER = auto()

class SignConv(Enum):
    CHOOSING = auto()
    CONFIRM = auto()
    SENDING = auto()

class DeckConv(Enum):
    ACTION = auto()
    CANCEL = auto()
    NAME = auto()
    DESCR = auto()
    CARDS = auto()
    NOTE = auto()
    TOKEN = auto()
    SIGN = auto()
    CHOOSING = auto() # SIGN
    CONFIRM = auto() # SIGN
    SENDING = auto() # SIGN

class WinConv(Enum):
    CHOOSING = auto()
    DESCRIPT = auto()
    
##def admin(func):
##    @wraps(func)
##    def wrapped(self, update, context, *args, **kwargs):
##        user_id = update.effective_user.id
##        if user_id != config.admin_id:
##            print(f"Unauthorized access denied for {user}.")
##            return
##        return func(self, update, context, *args, **kwargs)
##    return wrapped

def restrict(user_type):

    def decorator(func):
        @wraps(func)
        def command_func(self, update, context, *args, **kwargs):
            users_id = None
            if user_type is UserType.ADMIN:
                users_id = [id for id, in session.query(Player.id).filter(Player.is_admin==True).all()]
            elif user_type is UserType.PLAYER:
                users_id = [id for id, in session.query(Player.id).all()]
            if not update.effective_user.id in users_id:
                logging.info(f"Unauthorized access denied for {user}.")
                return
            return func(self, update, context, *args, **kwargs)
        return command_func
    
    return decorator

