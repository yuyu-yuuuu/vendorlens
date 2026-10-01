from redis import Redis
from rq import Queue, Worker

from vendorlens.config import get_settings


def main() -> None:
    connection = Redis.from_url(get_settings().redis_url)
    Worker([Queue("vendorlens", connection=connection)], connection=connection).work()


if __name__ == "__main__":
    main()

