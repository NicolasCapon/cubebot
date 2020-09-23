import config
import cubeHandler
import sys
import traceback
from telegram import ParseMode
from telegram.ext import Updater, CommandHandler
from telegram.utils.helpers import mention_html
from sqlalchemy.orm import sessionmaker
from sqlalchemy import create_engine

# Set up basic logging
import logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                    filename=config.log_file,
                    level=config.log_level)

def main():
    # Create the EventHandler and pass it your bot's token.
    updater = Updater(config.telegram_token, use_context=True)
    
    # Get the dispatcher to register handlers
    dp = updater.dispatcher
    
    # bH = botHandlers.BotHandlers(dp)
    cube_h = cubeHandler.CubeHandler(dp)
    
    # log all errors
    dp.add_error_handler(error)

    # help handler
    dp.add_handler(CommandHandler("help", send_help))
    
    # Start the Bot
    updater.start_polling()
    logging.info("Game Bot Started")
    
    # Run the bot until the you presses Ctrl-C or the process receives SIGINT,
    # SIGTERM or SIGABRT. This should be used most of the time, since
    # start_polling() is non-blocking and will stop the bot gracefully.
    updater.idle()

# this is a general error handler function. If you need more information about specific type of update, add it to the
# payload in the respective if clause
def error(update, context):
    # add all the dev user_ids in this list. You can also add ids of channels or groups.
    devs = [config.admin_id]
    # we want to notify the user of this problem. This will always work, but not notify users if the update is an 
    # callback or inline query, or a poll update. In case you want this, keep in mind that sending the message 
    # could fail
    if update.effective_message:
        text = "Hey. I'm sorry to inform you that an error happened while I tried to handle your update. " \
               "My developer(s) will be notified."
        update.effective_message.reply_text(text)
    # This traceback is created with accessing the traceback object from the sys.exc_info, which is returned as the
    # third value of the returned tuple. Then we use the traceback.format_tb to get the traceback as a string, which
    # for a weird reason separates the line breaks in a list, but keeps the linebreaks itself. So just joining an
    # empty string works fine.
    trace = "".join(traceback.format_tb(sys.exc_info()[2]))
    # lets try to get as much information from the telegram update as possible
    payload = ""
    # normally, we always have an user. If not, its either a channel or a poll update.
    if update.effective_user:
        payload += f' with the user {mention_html(update.effective_user.id, update.effective_user.first_name)}'
    # there are more situations when you don't get a chat
    if update.effective_chat:
        payload += f' within the chat <i>{update.effective_chat.title}</i>'
        if update.effective_chat.username:
            payload += f' (@{update.effective_chat.username})'
    # but only one where you have an empty payload by now: A poll (buuuh)
    if update.poll:
        payload += f' with the poll id {update.poll.id}.'
    # lets put this in a "well" formatted text
    text = f"Hey.\n The error <code>{context.error}</code> happened{payload}. The full traceback:\n\n<code>{trace}" \
           f"</code>"
    # and send it to the dev(s)
    for dev_id in devs:
        context.bot.send_message(dev_id, text, parse_mode=ParseMode.HTML)
    # we raise the error again, so the logger module catches it. If you don't use the logger module, use it.
    raise

def send_help(update, context):
    """Send commands and if admin request send additionnals admin commands"""
    text = ""
    if update.effective_user.id == config.admin_id:
        # Send all commands
        text += f"Pour inviter de nouveaux joueurs envoie leur {config.share_url}\n"\
                f"/init - initialize game\n"\
                f"/play [mode]- start playing game mode\n"\
                f"/win - stop game\n"

    text += f"/deck - start scanning\n"\
            f"/mydeck - edit your deck\n"

    if update.effective_user.id == config.admin_id:
        context.bot.send_message(update.effective_user.id, text=text)
    else:
        context.bot.send_message(chat_id=update.effective_chat.id,
                                 text=text)    
if __name__ == '__main__':
    main()
