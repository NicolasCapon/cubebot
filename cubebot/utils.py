import config
import logging
import os
import scryfall
import ndef
from random import shuffle
from tqdm import tqdm
from model import *
from sqlalchemy.orm import sessionmaker
from sqlalchemy import create_engine
from sqlalchemy import not_
from sqlalchemy.orm.exc import NoResultFound, MultipleResultsFound
import csv
import requests
import feedparser
from time import mktime, sleep
from datetime import datetime
from bs4 import BeautifulSoup
from pn532 import PN532_SPI
from deckstat_interface import get_sealed_url


def create_cube(name, cubecobra_id):
    c = Cube(name=name,
             cubecobra_id=cubecobra_id)
             #last_update=datetime.strptime("2020-04-01 13:58:21","%Y-%m-%d %H:%M:%S"))
    logging.info(f"Cube created: {c}")
    session.add(c)
    import_cubecobra(c)
    session.commit()
    logging.info("commit")
    return c


def import_cubecobra(cube, include_maybeboard=False, from_file=False):
    cards = get_cube_list(cube, from_file=from_file) #True if test on update
    cards = tqdm(cards, total=len(cards))
    cards.set_description(f"Cube creation")
    for card in cards:
        if card["Maybeboard"] == "true" and not include_maybeboard:
            continue
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
    s = scryfall.get_card_by_name(card.name, set=card.set_code, exact=True)
    if s.get("object", None) == "card":
        card.scryfall_id = s["id"]
        card.name = s["name"]
        card.image_url = scryfall.get_image_urls(s)[0]
        tokens_id = scryfall.get_related_tokens_id(s)
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
        response = requests.get(url)#, params=params)
        data = response.text
    print(response.content)
    csv_reader = csv.DictReader(data.splitlines())
    return list(csv_reader)


def quick_scan(cube):
    """Show card on screen then scan it to pear tag id to card in DB"""
    pn532 = PN532_SPI(debug=False, reset=20, cs=4)
    pn532.SAM_configuration()
    cards = session.query(CubeList, Card).join(Card).filter(CubeList.cube_id == cube.id).all()
    uids = []
    for cube_card, card in cards:
        logging.info(card.name)
        loop = True
        while loop:
            uid = pn532.read_passive_target(timeout=0.1)
            if uid is None or uid in uids:
                continue
            uids.append(uid)
            cube_card.uid = uid
            # write_string_to_tag(f"https://scryfall.com/card/{card.scryfall_id}", pn532)
            session.commit()
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


def scan_card_to_write_url(cube):
    """Scan card to write card url on tag"""
    pn532 = PN532_SPI(debug=False, reset=20, cs=4)
    pn532.SAM_configuration()
    loop = True
    logging.info("Place card on the scanner then wait before removing it")
    uids = []
    while loop:
        # Check if a card is available to read
        uid = pn532.read_passive_target(timeout=0.1)
        # Try again if no card is available.
        if uid is None:
            continue
        # Check if uid is known
        card = session.query(Card).join(CubeList).filter(CubeList.uid == uid).first()
        if card and not uid in uids:
            logging.info(f"{card.name} detected")
            url = "https://scryfall.com/cards/" + card.scryfall_id
            sleep(0.3)
            r = write_url_to_tag(url, pn532)
            if r:
                uids.append(uid)
                logging.info("WRITING SUCCESSFUL ! Remove card")
        elif not card:
            logging.info("Card not recognized or already scanned.")


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
    """WARNING: Only works on singleton cubes for now"""
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
    for i, u in enumerate(reversed(updates_to_proceed)):
        # logging.info(f"------Update du {u.published_parsed} ------")
        changes = str(u.summary).split("<br/>")
        changes = tqdm(changes, total=len(changes))
        changes.set_description(f"[{i+1}/{len(updates_to_proceed)}]{u.title}")
        for change in changes:
            c = BeautifulSoup(change, features="html.parser")
            cards = c.find_all("a")
            if c.span and c.span.string == "→" and len(cards) == 2:
                # Update card
                old_card_name = cards[0].string
                new_card_name = cards[1].string
                try:
                    card_to_update = session.query(CubeList).join(Card).filter(Card.name == old_card_name,
                                                                         CubeList.cube_id == cube.id).one()
                except NoResultFound as e:
                    logging.info(f"[{old_card_name}] {e}")
                finally:
                    # some cards with "//" in their name on scryfall are without "//" on cubecobra
                    card_to_update = session.query(CubeList).join(Card).filter(Card.name.like(old_card_name+" // %"),
                                                                         CubeList.cube_id == cube.id).first()
                if not card_to_update:
                    text = f"[{old_card_name} // %] also not found on DB for cube {cube.id}. "\
                            "Considere changing manually {old_card_name} → {new_card_name}"
                    logging.info(text)
                    continue
                
                new_card = Card(name=new_card_name)
                session.add(new_card)
                session.flush()
                card_to_update.card_id = new_card.id
                card_to_update.signature = None
                logging.info(f"~[{old_card_name} → {new_card_name}].")
            elif c.span and c.span.string == "+" and len(cards) == 1:
                # Add card 
                new_card_name = cards[0].string
                cube.cards.append(Card(name=new_card_name))
                logging.info(f"+[{new_card_name}]")
            elif c.span and c.span.string == "-" and len(cards) == 1:
                # Remove card
                old_card_name = cards[0].string
                try:
                    r = session.query(CubeList).join(Card).filter(Card.name == old_card_name,
                                                                CubeList.cube_id == cube.id).one()
                except NoResultFound as e:
                    logging.info(f"[{old_card_name}] {e}")
                    # some cards with "//" in their name on scryfall are without "//" on cubecobra
                    r = session.query(CubeList).join(Card).filter(Card.name.like(old_card_name+" // %"),
                                                                                 CubeList.cube_id == cube.id).all()
                    if len(r) == 1:
                        r = r[0]
                    else:
                        text = f"[{old_card_name} // %] also not found on DB for cube {cube.id}. "\
                               f"Considere deleting manually {old_card_name}"
                        logging.info(text)
                        continue
                    
                session.delete(r)
                logging.info(f"-[{old_card_name}]")
                    
        if i == len(updates_to_proceed)-1:
            # On last update, we update cards data based on csv
            # logging.info(">>>Load csv and crawl scryfall to fill missing cards data...")
            cube.last_update = datetime.fromtimestamp(mktime(u.published_parsed))
            cubelist = get_cube_list(cube)
            cubelist_db = session.query(Card).join(CubeList).filter(Card.set_code==None,
                                                                    CubeList.cube_id == cube.id).all()
            cubelist_db = tqdm(cubelist_db, total=len(cubelist_db))
            cubelist_db.set_description("Update cards info")
            for card_db in cubelist_db:
                for card in cubelist:
                    if card_db.name == card["Name"]:
                        # logging.info(f"Update data for {card_db.name}.")
                        card_db.set_code = card["Set"]
                        card_db.cmc=card["CMC"]
                        card_db.color=card["Color"]
                        card_db.type_line=card["Type"]
                        card_db.status=card["Status"]
                        card_db.tags=card["Tags"]
                        add_scryfall_infos(card_db)
                        break
            logging.info("Update Complete")
            # Add yes no option / telegram handler
            session.commit()
    return len(updates_to_proceed)


def write_url_to_tag(url, scanner, block_size=4, write_size=16):
    records = [ndef.UriRecord(url)]
    data = b"\x03<" + b"".join(ndef.message_encoder(records)) + b"\xfe"
    l = range(0, len(data), block_size)
    l = tqdm(l, total=len(l))
    l.set_description("Tag Writing")
    for n, i in enumerate(l):
        block = data[i:i+write_size].ljust(write_size, b"\x00")
        # Writing begins in fourth block
        r = scanner.mifare_classic_write_block(n+4, block)
        if not r:
            logging.error(f"Error while writing url [{url}] on {n+4}th block [{block}].")
            return r
    return r


def remove_games_from_cube(cube, state):
    """Remove all games and associated decks from game with specified state"""
    for game in cube.games:
        if game.state == state:
            for deck in game.decks:
                deck.cards[:] = []
                logging.info(f"Remove {deck}")
                game.decks.remove(deck)
                session.commit()
            game.decks[:] = []
            logging.info(f"Remove {game}")
            cube.games.remove(game)
    session.commit()


def set_boosters(draft):
    boosters = []
    if draft.cube.id in [1,5]:
        draft.booster_size = 9
        draft.round_num = 5
        cube_cards = session.query(Card).join(CubeList).filter(CubeList.cube_id == draft.cube.id,
                                                               Card.type_line != "Basic Land",
                                                               Card.tags != "Draft").all()
        shuffle(cube_cards)
        logging.info(f"{len(cube_cards)} cards selected.")
        n = 0
        for drafter in draft.drafters:
            for i in range(draft.round_num):
                booster_cards = []
                # 9 cards per boost
                while len(booster_cards) < draft.booster_size:
                    booster_cards.append(cube_cards.pop())
                boosters.append(Booster(id=n, cards=booster_cards))
                n += 1

    elif draft.cube.id == 4:
        # Custom function for Greg Cube Draft
        commanders = session.query(Card).join(CubeList).filter(CubeList.cube_id == 3,
                                                               Card.type_line != "Basic Land",
                                                               not_(Card.tags.contains('partnerWith'))).all()
        # Manually add partners
        for partner in draft.partners:
            commanders.append(session.query(Card).join(CubeList).filter(CubeList.cube_id == 3,
                                                                        Card.name == partner["name"]).first())

        cube_cards = session.query(Card).join(CubeList).filter(CubeList.cube_id == 4,
                                                               Card.type_line != "Basic Land").all()

        shuffle(cube_cards)
        shuffle(commanders)

        n = 0
        for drafter in draft.drafters:
            # 5 main boosters
            for i in range(5):
                booster_cards = []
                # 12 cards per boost
                while len(booster_cards) < 12:
                    booster_cards.append(cube_cards.pop())
                boosters.append(Booster(id=n, cards=booster_cards))
                n += 1

        command_tower = session.query(Card).filter(Card.name == "Command Tower").first()
        # Add commander last to be poped first during draft
        for drafter in draft.drafters:
            # Every player get the card 'command tower' to build his deck
            drafter.pool.append(command_tower)
            booster_cards = []
            # 6 cards per boost
            while len(booster_cards) < 6:
                booster_cards.append(commanders.pop())
            boosters.append(Booster(id=n, cards=booster_cards))
            n += 1

    else:
        logging.info("No specific configuration for this cube")
        return None, None

    draft.boosters = boosters

    content = ""
    for card in cube_cards:
        content += card.name + "\n"
        filename = "Cartes restantes.txt"

    return content, filename


if __name__ == "__main__":
    """To test update :
    - create cube with last_update = 2020-04-01 13:58:21
    - load from csv in test directory
    """
    logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                       # filename=config.log_file,
                        level=config.log_level)

    # cubes = session.query(Cube).all()
    # for cube in cubes:
      #   print(cube.id, cube.name, len(cube.cards))
        
    cube = session.query(Cube).filter(Cube.id == 5).first()
    print(len(cube.cards))
    cards = session.query(Card).filter(Card.tags == "Draft").all()
    for card in cards:
        cards_to_change = session.query(Card).join(CubeList).filter(CubeList.cube_id == 5, 
                                                                    Card.name == card.name).all()
        for c in cards_to_change:
            c.tags = "Draft"
    # session.commit()
    print(session.query(Card).join(CubeList).filter(CubeList.cube_id == 5, Card.tags == "Draft").all())
    # import_cubecobra(cube)
    # session.commit()
    # create_cube("yolocube test", "6075b3211e5a7210494c053d")
    
    # card = [card for card in get_cube_list(cube=c) if card.get("Name") == "Lathiel, the Bounteous Dawn"][0]
    # c = Card(name=card["Name"],
    #          set_code=card["Set"],
    #          cmc=card["CMC"],
    #          color=card["Color"],
    #          type_line=card["Type"],
    #          status=card["Status"],
    #          tags=card["Tags"])
    # add_scryfall_infos(c)
    # cube = session.query(Cube).filter(Cube.id == 3).one()
    # cube.cards.append(c)
    # print(len(cube.cards))
    # session.commit()
    # c = session.query(Cube).filter(Cube.id == 3).one()
    # update_cube(cube=c)
