import argparse, logging, os.path
from time import time

from bottle import get, run, template
from bottle.ext.websocket import GeventWebSocketServer
from bottle.ext.websocket import websocket
from gevent.lock import BoundedSemaphore

import deepspeech
import numpy as np

logger = logging.getLogger(__name__)
logging.basicConfig(level=20,
    format="%(asctime)s.%(msecs)03d: %(name)s: %(levelname)s: %(funcName)s(): %(message)s",
    datefmt="%Y-%m-%d %p %I:%M:%S",
    )

parser = argparse.ArgumentParser(description='')
parser.add_argument('-m', '--model', required=True,
                    help='Path to the model (protocol buffer binary file, or directory containing all files for model)')
parser.add_argument('-a', '--alphabet', nargs='?', const='alphabet.txt',
                    help='Path to the configuration file specifying the alphabet used by the network. Default: alphabet.txt')
parser.add_argument('-l', '--lm', nargs='?', const='lm.binary',
                    help='Path to the language model binary file. Default: lm.binary')
parser.add_argument('-t', '--trie', nargs='?', const='trie',
                    help='Path to the language model trie file created with native_client/generate_trie. Default: trie')
parser.add_argument('--lw', type=float, default=1.5,
                    help='The alpha hyperparameter of the CTC decoder. Language Model weight. Default: 1.5')
parser.add_argument('--vwcw', type=float, default=2.25,
                    help='Valid word insertion weight. This is used to lessen the word insertion penalty when the inserted word is part of the vocabulary. Default: 2.25')
parser.add_argument('--bw', type=int, default=1024,
                    help='Beam width used in the CTC decoder when building candidate transcriptions. Default: 1024')
parser.add_argument('-la', '--lm_alpha', type=float, default=0.75,
                        help=f"The alpha hyperparameter of the CTC decoder. Language Model weight. Default: 0.75")
parser.add_argument('-lb', '--lm_beta', type=float, default=1.85,
                        help=f"The beta hyperparameter of the CTC decoder. Word insertion bonus. Default: 1.85")
parser.add_argument('-p', '--port', default=8080,
                    help='Port to run server on. Default: 8080')
parser.add_argument('--debuglevel', default=20,
                    help='Debug logging level. Default: 20')
ARGS = parser.parse_args()

logging.getLogger().setLevel(int(ARGS.debuglevel))

gSem = BoundedSemaphore(1)  # Only one Deepspeech instance available at a time

if os.path.isdir(ARGS.model):
    model_dir = ARGS.model
    ARGS.model = os.path.join(model_dir, 'output_graph.pb')
    ARGS.alphabet = os.path.join(model_dir, ARGS.alphabet if ARGS.alphabet else 'alphabet.txt')
    if ARGS.lm: ARGS.lm = os.path.join(model_dir, ARGS.lm)
    if ARGS.trie: ARGS.trie = os.path.join(model_dir, ARGS.trie)

LM_WEIGHT = ARGS.lw
VALID_WORD_COUNT_WEIGHT = ARGS.vwcw
BEAM_WIDTH = ARGS.bw
N_FEATURES = 26
N_CONTEXT = 9

print('Initializing model...')
logger.info("ARGS.model: %s", ARGS.model)
logger.info("ARGS.alphabet: %s", ARGS.alphabet)

model = deepspeech.Model(ARGS.model, BEAM_WIDTH)
if ARGS.lm and ARGS.trie:
        logging.info("ARGS.lm: %s", ARGS.lm)
        logging.info("ARGS.trie: %s", ARGS.trie)
        model.enableDecoderWithLM(ARGS.lm, ARGS.trie, ARGS.lm_alpha, ARGS.lm_beta)

@get('/recognize', apply=[websocket])
def recognize(ws):
    logger.debug("new websocket")
    start_time = None
    gSem_acquired = False

    while True:
        data = ws.receive()
        # logger.log(5, "got websocket data: %r", data)

        if isinstance(data, bytearray):
            # Receive stream data
            if not start_time:
                # Start of stream (utterance)
                start_time = time()
                sctx = model.createStream()
                assert not gSem_acquired
                # logger.debug("acquiring lock for deepspeech ...")
                gSem.acquire(blocking=True)
                gSem_acquired = True
                # logger.debug("lock acquired")
            model.feedAudioContent(sctx, np.frombuffer(data, np.int16))

        elif isinstance(data, str) and data == 'EOS':
            # End of stream (utterance)
            eos_time = time()
            text = model.finishStream(sctx)
            logger.info("recognized: %r", text)
            logger.info("    time: total=%s post_eos=%s", time()-start_time, time()-eos_time)
            ws.send(text)
            # FIXME: handle ConnectionResetError & geventwebsocket.exceptions.WebSocketError
            # logger.debug("releasing lock ...")
            gSem.release()
            gSem_acquired = False
            # logger.debug("lock released")
            start_time = None

        else:
            # Lost connection
            logger.debug("dead websocket")
            if gSem_acquired:
                # logger.debug("releasing lock ...")
                gSem.release()
                gSem_acquired = False
                # logger.debug("lock released")
            break

@get('/')
def index():
    return template('index')

run(host='127.0.0.1', port=ARGS.port, server=GeventWebSocketServer)

# python server.py --model ../models/daanzu-30330/output_graph.pb --alphabet ../models/daanzu-30330/alphabet.txt --lm ../models/daanzu-30330/lm.binary --trie ../models/daanzu-30330/trie
# python server.py --model ../models/daanzu-30330.2/output_graph.pb --alphabet ../models/daanzu-30330.2/alphabet.txt --lm ../models/daanzu-30330.2/lm.binary --trie ../models/daanzu-30330.2/trie
