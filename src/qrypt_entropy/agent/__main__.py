import sys

from qrypt_entropy.agent.chat import chat_turn

if __name__ == "__main__":
    print(chat_turn(" ".join(sys.argv[1:])))
