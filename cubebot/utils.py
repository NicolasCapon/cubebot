import config
import logging
import os
import scryfall
from tqdm import tqdm
from model import session, Cube, CubeList, Card, Game, Player, Deck, DeckList, Token
from sqlalchemy.orm import sessionmaker
from sqlalchemy import create_engine
import csv
import requests
import feedparser
from time import mktime
from datetime import datetime
from bs4 import BeautifulSoup
from pn532 import PN532_SPI

def create_cube():
    # session.query(Game).delete()
    c = Cube(name="Multiplayer Yolo Cube",
             cubecobra_id = "5dd42bd004af383a21c92eb9")
    logging.info(f"Cube created: {c}")
    p = Player(id=config.admin_id, name="Nicolas", is_admin=True)
    logging.info(f"Player added: {p}")
    session.add(c)
    session.add(p)
    import_cubecobra(c)
    import_basic_lands(c)
    session.commit()
    logging.info("commit")
    return c
    
def import_cubecobra(cube):
    cards = get_cube_list(cube, from_file=False)
    for card in tqdm(cards, total=len(cards)):
        c = Card(name=card["Name"],
                 set_code=card["Set"],
                 cmc=card["CMC"],
                 color=card["Color"],
                 type_line=card["Type"],
                 status=card["Status"],
                 tags=card["Tags"])
        add_scryfall_infos(c)
        cube.cards.append(c)
        
    logging.info("CubeCobra succesfully imported")
    return cube

def add_scryfall_infos(card):
    """Add scryfall id, image_url
       Then create related token and link them to card"""
    s = scryfall.search(name=card.name, set=card.set_code)
    data = s.get("data", [None])[0]
    if data.get("object", None) == "card":
        card.scryfall_id = data["id"]
        card.image_url = scryfall.get_image_urls(data)[0]
        tokens_id = scryfall.get_related_tokens_id(data)
        for token_id in tokens_id:
            t = scryfall.get_card_by_id(token_id)
            color = scryfall.get_card_color(t)
            # Check for existing tokens
            token = session.query(Token).filter(Token.name==t["name"],
                                                Token.power==t.get("power", None),
                                                Token.toughness==t.get("toughness", None),
                                                Token.color==color).first()
            # If token doesnt exist we create it
            if not token:
                token = Token(name = t["name"],
                              power = t.get("power", None),
                              toughness = t.get("toughness", None),
                              color = scryfall.get_card_color(t),
                              image_url = scryfall.get_image_urls(t)[0],
                              scryfall_id = t["id"])
            # Add related token to card
            card.tokens.append(token)
        
    
def import_basic_lands(cube):
    basics = ["Plains", "Island", "Swamp", "Mountain", "Forest"]
    for card in basics:
        c = Card(name=card,
                 set_code="UNH",
                 cmc=0,
                 color=None,
                 type_line="Basic Land",
                 status="owned")
        cube.cards.append(c)
    
    logging.info("Basic Lands succesfully imported")

def get_cube_list(cube, from_file=False):
    if from_file:
        # Old file for testing updates
        cube_path = os.path.join(config.project_dir, "test", "test_export_cube.csv")
        with open(cube_path, "r") as f:
            data = f.read()
    else:
        params = {"primary": "Color Category",
                  "secondary": "Types-Multicolor",
                  "tertiary": "CMC2"}
        
        url = "https://cubecobra.com/cube/download/csv/" + cube.cubecobra_id
        logging.info(f"fetch cube list on {url}")
        response = requests.get(url, params=params)
        data = response.text

    csv_reader = csv.DictReader(data.splitlines())
    return list(csv_reader)

def write_string_to_tag(data, pn532):
    b = data.encode()
    chunks = [b[i:i+4] for i in range(0, len(b), 4)]
    for i, chunk in enumerate(chunks):
        result = pn532.ntag2xx_write_block(i, chunk)
        print(f"Writing data: {result}")
    
def quick_scan(cube):
    pn532 = PN532_SPI(debug=False, reset=20, cs=4)
    pn532.SAM_configuration()
    cards = session.query(CubeList, Card).join(Card).filter(CubeList.cube_id == cube.id).all()
    uids = []
    for cube_card, card in cards:
        logging.info(name)
        loop = True
        while loop:
            uid = pn532.read_passive_target(timeout=0.1)
            if uid is None or uid in uids:
                continue
            uids.append(uid)
            cube_card.uid = uid
            write_string_to_tag(f"https://scryfall.com/card/{card.scryfall_id}", pn532)
            loop = False
            
    
def scan_card_for_DB(cube):
    # TODO write gatherer link of the card to nfc chip for phone scan - ntag2xx_write_block
    # https://blog.foulquier.info/tutoriels/iot/installation-de-la-carte-nfc-pn532-sur-un-arduino-et-ecriture-d-un-message-ndef-sur-un-tag-mifare-classic
    pn532 = PN532_SPI(debug=False, reset=20, cs=4)
    pn532.SAM_configuration()
    loop = True
    logging.info("Place card on the scanner one by one... Type 'Done' to stop process.")
    while loop:
        # Check if a card is available to read
        uid = pn532.read_passive_target(timeout=0.1)
        # Try again if no card is available.
        if uid is None:
            continue
        # Check if uid is known
        logging.info('Enter card name:')
        card_name = input()
        card_list = session.query(CubeList).join(Card).filter(Card.name.like(card_name+"%")).all()
        logging.info(card_list)
        if len(card_list) == 1:
            card = card_list[0]
            card.uid = uid
            session.commit()
            # ntag2xx_write_block(1, url)
            logging.info(f"Saved: {card}\nPlace next card on the scanner...")
        elif card_name == "Done":
            logging.info("Scan for DB done.")
            return cube
        else:
            logging.info("Multiple cards found, try again")

    return cube

def test_scan(cube):
    cubelist = session.query(CubeList).join(Card).filter(CubeList.uid != None).all()
    pn532 = PN532_SPI(debug=False, reset=20, cs=4)
    pn532.SAM_configuration()
    logging.info("Start scanning to see if it works...")
    loop = True
    while loop:
        # Check if a card is available to read
        uid = pn532.read_passive_target(timeout=0.1)
        # Try again if no card is available.
        if uid is None:
            continue
        logging.info(uid)
        # Check if uid is known
        c = next((c for c in cubelist if c.uid == uid), None)
        if c:
           logging.info(c.card)

def update_cube(cube):
    rss = "https://cubecobra.com/cube/rss/" + cube.cubecobra_id
    updates = feedparser.parse(rss).entries
    t1 = mktime(cube.last_update.timetuple())
    # t1 = mktime(updates[3].published_parsed)
    updates_to_proceed = []
    i = 0
    while i < len(updates) and t1 - mktime(updates[i].published_parsed) < 0:
        soup = BeautifulSoup(updates[i].summary,
                             features="html.parser",
                             multi_valued_attributes=None)
        if soup.div.get('class', None) == "change-set":
            updates[i].summary = soup.div
            updates_to_proceed.append(updates[i])
        i += 1
    
    logging.info(f"{len(updates_to_proceed)} update(s) found.")
    updates_to_proceed = tqdm(reversed(updates_to_proceed), total=len(updates_to_proceed))
    updates_to_proceed.set_description("Total Updates")
    for i, u in enumerate(updates_to_proceed):
        logging.info(f"------Update du {u.published_parsed} ------")
        changes = str(u.summary).split("<br/>")
        changes = tqdm(changes, total=len(changes))
        changes.set_description(f"{u.title}")
        for change in changes:
            c = BeautifulSoup(change, features="html.parser")
            cards = c.find_all("a")
            if c.span and c.span.string == "→" and len(cards) == 2:
                # Update card
                old_card_name = cards[0].string
                new_card_name = cards[1].string
                old_card = session.query(Card).join(CubeList).filter(Card.name == old_card_name,
                                                                     CubeList.cube_id == cube.id).one()
                new_card = Card(name=new_card_name)
                card_to_update = session.query(CubeList).filter(CubeList.card_id == old_card.id,
                                                                CubeList.cube_id == cube.id).one()
                card_to_update.card_id = new_card.id
                card_to_update.signature = None
                logging.info(f"{old_card_name} est remplacé par {new_card_name}.")
            elif c.span and c.span.string == "+" and len(cards) == 1:
                # Add card
                new_card_name = cards[0].string
                cube.append(Card(name=new_card_name))
                logging.info(f"{new_card_name} est ajouté.")
            elif c.span and c.span.string == "-" and len(cards) == 1:
                # Remove card
                old_card_name = cards[0].string
                session.query(CubeList).join(Card).filter(Card.name == old_card_name,
                                                          CubeList.cube_id == cube.id).delete()
                logging.info(f"{old_card_name} est supprimé.")
        if i == len(updates_to_proceed)-1:
            # On last update, we update cards data based on csv
            logging.info(">>> Last Update, load csv to fill missing cards data...")
            cube.last_update = datetime.fromtimestamp(mktime(u.published_parsed))
            cubelist = get_cube_list(cube)
            cubelist_db = session.query(Card).join(CubeList).filter(Card.set_code==None,
                                                                    CubeList.cube_id == cube.id).all()
            cubelist_db = tqdm(cubelist_db, total=len(cubelist_db))
            cubelist_db.set_description(f"{u.title}")
            for card_db in cubelist_db:
                for card in cubelist:
                    if card_db.name == card["Name"]:
                        logging.info(f"Update data for {card_db.name}.")
                        card_db.set_code = card["Set"]
                        card_db.cmc=card["CMC"]
                        card_db.color=card["Color"]
                        card_db.type_line=card["Type"]
                        card_db.status=card["Status"]
                        card_db.tags=card["Tags"]
                        add_scryfall_infos(card_db)
                        break
            logging.info("Commit")
            session.commit()
    return len(updates_to_proceed)


if __name__ == "__main__":
    """To test update :
    - create cube with last_update = 2020-04-01 13:58:21
    - load from csv in test directory
    """
    logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                       # filename=config.log_file,
                        level=config.log_level)
    cube = session.query(Cube).filter(Cube.id==1).first()
    # quick_scan(cube)
    a = "https://scryfall.com/card/894321".encode()
    print(a)
    print(len(a))
    # cube = create_cube()
    # update_cube(cube)
    # cube = scan_card_for_DB(cube)
    # test_scan(cube)
    
##    g = Game()
##    cube.games.append(g)
##    d = Deck(name="deckTest")
##    g.decks.append(d)
##    c1 = Card(name="Snap")
##    d.cards.append(c1)
##    c2 = Card(name="Test")
##    d.cards.append(c2)
##    d.cards.append(c2)
##    card = session.query(DeckList).join(Card).filter(Card.name=="Test").first()


    # logging.info(card.card in d.cards)
    # d.cards.remove(card.card)
    # logging.info(d.cards)
    # logging.info(session.delete(card))
    # logging.info(session.query(DeckList).filter(Deck.id==d.id).all())
    
    
