import logging
from pn532 import PN532_SPI


class NFC_Scanner():

    is_on = False
    debug = False
    timeout = 0.1
    reset = 20
    cs = 4
    pn532 = None

    def __init__(self):
        self.pn532 = PN532_SPI(debug=self.debug, reset=self.reset, cs=self.cs)
        self.pn532.SAM_configuration()

    def start(self, behaviour, *args, **kwargs):
        self.is_on = True
        logging.info("NFC_Scanner turned ON")
        while self.is_on:
            uid = None
            try:
                uid = self.pn532.read_passive_target(timeout=self.timeout)
            except RuntimeError as e:
                logging.exception(e)
            if uid is None:
                continue
            behaviour(uid, *args, **kwargs)

    def stop(self):
        self.is_on = False
        logging.info("NFC_Scanner turned OFF")

if __name__ == "__main__":
    scan = NFC_Scanner()
    def hello(uid, ok):
        logging.info(f"test uid = [{uid}] {ok}")

    scan.start(hello, ["test1", "test2"])

