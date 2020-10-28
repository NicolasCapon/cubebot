import re
import json
import requests
import logging
import datetime

def get_deck_url(deck):
    """Get deck url on deckstat.net from given deck.
    Code inspired from cockatrice:
    https://github.com/Cockatrice/Cockatrice/blob/master/cockatrice/src/deckstats_interface.cpp"""
    if not deck.cards:
        return None
    #Prepare request
    decklist = ""
    for i, deck_card in enumerate(deck.cards):
        br = "\n"
        note = " #"
        decklist += f"{deck_card.amount}x {deck_card.card.name}{note+deck_card.note if deck_card.note else ''}{br if i<len(deck.cards)-1 else ''}"
    return get_url(decklist, deck.name)

def get_sealed_url(cards, title):
    decklist = ""
    for i, card in enumerate(cards):
        br = "\n"
        decklist += f"1x [{card.set_code}] {card.name}{br if i<len(cards)-1 else ''}"
    
    timestamp = datetime.date.today().strftime("%d-%m-%Y")
    decktitle = f"{title} du {timestamp}"
    return get_url(decklist, decktitle)

def get_url(decklist, decktitle):
    url = 'https://deckstats.net/index.php'
    headers = {"Content-type":"application/x-www-form-urlencoded"}
    data = {"deck": decklist, "decktitle":decktitle.encode('latin-1')}
    # Request and handle error status
    r = requests.post(url, data=data, headers=headers)
    if r.ok:
        # Regex to find deck url in page content
        regex = "<meta property=\"og:url\" content=\"([^\"]+)\""
        m = re.findall(regex, r.text)
        if m:
            return m[0]
        else:
            logging.info(f"Match not found in page content for deck [{decktitle}]")
            return None
    else:
        logging.info("Deckstat request failed {0}.".format(r))
        return None
        
if __name__ == "__main__":
    from model import Card, Deck
    c1 = Card(name="Snap")
    c2 = Card(name="Tropical Island")
    deck = Deck(name="test")
    deck.cards.append(c1)
    deck.cards.append(c2)
    print(get_deck_url(deck))
