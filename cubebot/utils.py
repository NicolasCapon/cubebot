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
import csv
import requests
import feedparser
from time import mktime, sleep
from datetime import datetime
from bs4 import BeautifulSoup
from pn532 import PN532_SPI
from deckstat_interface import get_sealed_url

def create_cube():
    c = Cube(name="Multiplayer Yolo Cube",
             cubecobra_id = "5dd42bd004af383a21c92eb9")
             #last_update=datetime.strptime("2020-04-01 13:58:21","%Y-%m-%d %H:%M:%S"))
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
    
def import_cubecobra(cube, include_maybeboard=False):
    cards = get_cube_list(cube, from_file=False) #True if test on update
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
        response = requests.get(url, params=params)
        data = response.text

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
                card_to_update = session.query(CubeList).join(Card).filter(Card.name == old_card_name,
                                                                     CubeList.cube_id == cube.id).one()
                # card_to_update = session.query(CubeList).filter(CubeList.card_id == old_card.id,
                #                                                 CubeList.cube_id == cube.id).one()
                new_card = Card(name=new_card_name)
                session.add(new_card)
                session.flush()
                card_to_update.card_id = new_card.id
                card_to_update.signature = None
                logging.info(f"~[{old_card_name} → {new_card_name}].")
            elif c.span and c.span.string == "+" and len(cards) == 1:
                # Add card 
                new_card_name = cards[0].string
                cube.append(Card(name=new_card_name))
                logging.info(f"+[{new_card_name}]")
            elif c.span and c.span.string == "-" and len(cards) == 1:
                # Remove card
                old_card_name = cards[0].string
                session.query(CubeList).join(Card).filter(Card.name == old_card_name,
                                                          CubeList.cube_id == cube.id).delete()
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
    if draft.cube.id == 1:
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
                # 12 cards per boost
                while len(booster_cards) < draft.booster_size:
                    booster_cards.append(cube_cards.pop())
                boosters.append(Booster(id=n, cards=booster_cards))
                n += 1

    elif draft.cube.id == 2:
        # Custom function for Greg Cube Draft
        commanders = session.query(Card).join(CubeList).filter(CubeList.cube_id == 3,
                                                               Card.type_line != "Basic Land",
                                                               not_(Card.tags.contains('partnerWith'))).all()
        # Manually add partners
        for partner in draft.partners:
            commanders.append(session.query(Card).join(CubeList).filter(CubeList.cube_id == 3,
                                                                        Card.name == partner["name"]).first())

        cube_cards = session.query(Card).join(CubeList).filter(CubeList.cube_id == 2,
                                                               Card.type_line != "Basic Land").all()

        shuffle(cube_cards)
        shuffle(commanders)

        n = 0
        for drafter in draft.drafters:
            # 5 main boosters
            for i in range(5):
                booster_cards = []
                # 12 cards per boost
                while len(booster_cards) < 12:#12
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

#     c = Cube(name="Saltless City EDH Cube", cubecobra_id="5e25e166a9a76f129adb0893")
#     session.add(c)
#     import_cubecobra(c)
#     session.commit()
#
#     c1 = Cube(name="Commander Pool", cubecobra_id="5e74a7e740eaf0158e316de3")
#     session.add(c1)
#     import_cubecobra(c1)
#     session.commit()
#
#     regalias = """Âme Hellbent.jpg;https://drive.google.com/file/d/154iIEfjDs1k4ofku5FGFUv2XKx0DC3lJ/view?usp=sharing
# And you tap tap tap....jpg;https://drive.google.com/file/d/1aIiyNClBWgTypgWAltO_fkcAs4-2aVz-/view?usp=sharing
# Beastcallers Scepter.jpg;https://drive.google.com/file/d/1KDea0Nl7MfP5ms-yzqoj7Srxx7KQB8tC/view?usp=sharing
# Cant touch me.jpg;https://drive.google.com/file/d/1aVLXpnawj6J-Tv9ol8cSj3_zkPbnoWMJ/view?usp=sharing
# Chromatic Style.jpg;https://drive.google.com/file/d/1_V_n7kZs-bamtNwE5VqKtQpR4cJTPMoB/view?usp=sharing
# Clockwork Bauble.jpg;https://drive.google.com/file/d/1XaCQwQQRwWA_aVwPzICN34gjFrIAr5JJ/view?usp=sharing
# Dragonform Cloak.jpg;https://drive.google.com/file/d/1cy3r4n0mOULZVVlB5Tq7sN1Ia-dawpwS/view?usp=sharing
# Enchanters Scepter.jpg;https://drive.google.com/file/d/1uISmf6Xoda4e45dJrT9ZBg9MlOoLwkSl/view?usp=sharing
# Gayvins style.jpg;https://drive.google.com/file/d/1xpunpen3SBFlVsKuK15AsP21sGbm8iKV/view?usp=sharing
# Golden Eggs.jpg;https://drive.google.com/file/d/1nRNZFRGh3P8cWE0ugMi7Wmsnlf_A6Gve/view?usp=sharing
# Im a Legend.jpg;https://drive.google.com/file/d/1ABGGH_rIwKCbfsO5tx0NMnpxZJmM_l74/view?usp=sharing
# Iron Armor.jpg;https://drive.google.com/file/d/1lovSpl4R-fLqcYw-Jx6Ufj0MhbO51NMs/view?usp=sharing
# Iron Scepter.jpg;https://drive.google.com/file/d/1w4KOccHVoNBWkqFTeY-oq4dpOcuEm36-/view?usp=sharing
# Mark of Life and Death.jpg;https://drive.google.com/file/d/1PR2U0ERwxXjpT03H-oHG08bhJ6lsy5vX/view?usp=sharing
# Mark of the Hive.jpg;https://drive.google.com/file/d/15_yt8w8JKdtKuY8BbW4eQs1DMkNlXoUf/view?usp=sharing
# Mirrored Tattoos.jpg;https://drive.google.com/file/d/1a0zQRA7CFMH5S1bAAqUPE25CQFMaMOo_/view?usp=sharing
# Panard de Monique On.jpg;https://drive.google.com/file/d/1cvWur_pNu1qqoKVvYgIbBG6nQX5MWjQJ/view?usp=sharing
# Peace and Love Bitch.jpg;https://drive.google.com/file/d/1YMvImoBHpGVrnfhceY1GlEUeJfcbt7dE/view?usp=sharing
# Political Gift.jpg;https://drive.google.com/file/d/1rTmMFxnU5IWB3gIyMIPi6mGNWCNiFNeV/view?usp=sharing
# Scepter of Conjuration.jpg;https://drive.google.com/file/d/11BydeQzXo7efn0XB6eM5sPP3I_mmCGVn/view?usp=sharing
# Scepter of Destiny.jpg;https://drive.google.com/file/d/12jaXbIjRVsPkETqsn_Qoe2tADF4Ho4eI/view?usp=sharing
# Scepter of the Worthy.jpg;https://drive.google.com/file/d/113vJM012W_OtqHV9klAVHQiOnUUf0GFv/view?usp=sharing
# Spellslinger.jpg;https://drive.google.com/file/d/1J5KforfmasXNMKJHbsjwbMl286UxOiuT/view?usp=sharing
# Staff of the Heroes.jpg;https://drive.google.com/file/d/15g9xPEvkbl4NhqczILKVsm9lzcWdspkh/view?usp=sharing
# Staff of the Man A-Fixing.jpg;https://drive.google.com/file/d/1iA9SsVz-qhIMFCVhpUIs6ZdbU-egJVT4/view?usp=sharing
# Staff of the Walkers.jpg;https://drive.google.com/file/d/1eqA-1wNJ_Vtc_P0_R30VTY8ks4e20hRK/view?usp=sharing
# Suit Up .jpg;https://drive.google.com/file/d/1IXO98D6uG4UYYeuLmiF9CULY8rD_vcYs/view?usp=sharing
# Trail of Opportunity.jpg;https://drive.google.com/file/d/1Ylqz4qb1aeRSeXPD3OrhcmydY7sscu7v/view?usp=sharing
# Tree OCloak.jpg;https://drive.google.com/file/d/1NFi5KCWuGKwSg4D1pxFeEDRGooSiPtvL/view?usp=sharing
# Tribal King Flag.jpg;https://drive.google.com/file/d/1CIOHnnyXUaiaHeOYkcDe7UyvkSqrzgc2/view?usp=sharing
# Tribal King Seal.jpg;https://drive.google.com/file/d/1IigQcNaAAr5XALrcHM5Kp0ind34YnWwg/view?usp=sharing
# Uncommander.jpg;https://drive.google.com/file/d/1bXfM444nNGUrzoBrPn13E5MD-NcXDI29/view?usp=sharing
# Underworld Bar Gains.jpg;https://drive.google.com/file/d/17UuCkrvlqSCupaIV1jB_agmOZOIk40_w/view?usp=sharing
# Violet Lotus Cloak.jpg;https://drive.google.com/file/d/1WbAY0Lt6uQxL-hiuu4t-6I00DEAOfVJK/view?usp=sharing
# Walker Texas Ranger.jpg;https://drive.google.com/file/d/1zDHD89-VAZiu8DeG7YXvDlfdcds_SRnT/view?usp=sharing
# Wandering Eye.jpg;https://drive.google.com/file/d/1Qp7Nz9y0fiYOO_Njf4zlZ76KCVwOpTVW/view?usp=sharing"""
#
#     for regalia in regalias.split("\n"):
#         r = regalia.split(";")
#         name = r[0].split(".jpg")[0]
#         url = r[1]
#         c.cards.append(Card(name=name, type_line="Regalia", image_url=url))
#
#     logging.info("Regalias imported")
#     session.commit()
    
    
