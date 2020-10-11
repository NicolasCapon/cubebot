import os
import errno
import logging
from vlc import State, Instance
from time import sleep
from pn532 import PN532_SPI
        
def audio_scan(cube, context):
    import config
    from model import session, CubeList, DeckList, Card
    pn532 = PN532_SPI(debug=False, reset=20, cs=4)
    pn532.SAM_configuration()
    loop = True
    logging.info("audio scan ready")
    while loop:
        # Check if a card is available to read
        uid = pn532.read_passive_target(timeout=0.1)
        # Try again if no card is available.
        if uid is None:
            continue
        # Check if uid is known
        # cube, card, deck = session.query(CubeList, Card, DeckList).join(Card).join(DeckList).filter(CubeList.cube_id == cube.id,
        #                    CubeList.uid == uid).filter(or_(CubeList.signature != None, DeckList.note != None)).first()

##        cubelist, decklist = session.query(CubeList, DeckList).filter(CubeList.card_id == DeckList.card_id).filter(CubeList.cube_id == cube.id,
##                             CubeList.uid == uid).filter(or_(CubeList.signature != None, DeckList.note != None)).first()

        cubelist, decklist= None, None
        result = session.query(CubeList, DeckList).filter(CubeList.card_id == DeckList.card_id).filter(CubeList.cube_id == cube.id,
                             CubeList.uid == uid).first()
        print(result)
        if result is not None:
            cubelist, decklist = result
            if decklist.note:
                # TODO: envoyer la note en mp aux joueurs
                context.bot.send_message(chat_id=config.chat_id,
                                         text=decklist.note)
                if not cubelist.signature:
                    sleep(3)
            if cubelist.signature:
                s = os.path.join(config.src_dir, "resources", "sounds", cubelist.signature)
                play_sound(s)

def audio_scan_test(cube):
    import config
    from model import session, CubeList, DeckList, Card
    pn532 = PN532_SPI(debug=False, reset=20, cs=4)
    pn532.SAM_configuration()
    loop = True
    logging.info("audio scan ready")
    while loop:
        # Check if a card is available to read
        uid = pn532.read_passive_target(timeout=0.1)
        # Try again if no card is available.
        if uid is None:
            continue
        # Check if uid is known
        # cube, card, deck = session.query(CubeList, Card, DeckList).join(Card).join(DeckList).filter(CubeList.cube_id == cube.id,
        #                    CubeList.uid == uid).filter(or_(CubeList.signature != None, DeckList.note != None)).first()

        # cubelist, decklist = session.query(CubeList).filter(CubeList.card_id == DeckList.card_id).filter(CubeList.cube_id == cube.id,
                             # CubeList.uid == uid).filter(or_(CubeList.signature != None, DeckList.note != None)).first()
        cubelist = session.query(CubeList).filter(CubeList.cube_id == cube.id, CubeList.uid == uid).filter(CubeList.signature != None).first()
        if cubelist.signature:
            s = os.path.join(config.src_dir, "resources", "sounds", cubelist.signature)
            play_sound(s)

def play_sound(sound, wait_until_done=True):
    """Play various type of sound based on vlc media player
    Doc: https://www.olivieraubert.net/vlc/python-ctypes/doc/"""
    if not os.path.exists(sound):
        raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), sound)
    vlc = Instance("--quiet") # --quiet to avoid vlcpulse error
    player = vlc.media_player_new()
    media = vlc.media_new(sound)
    player.set_media(media)
    player.play()
    while wait_until_done and player.get_state() != State.Ended:
        continue
    return

if __name__ == "__main__":
    pass
