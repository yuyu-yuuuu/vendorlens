from vendorlens.checkpoint import checkpoint_connection_string


def test_checkpoint_url_removes_sqlalchemy_driver_marker():
    assert (
        checkpoint_connection_string("postgresql+psycopg://user:pass@db:5432/app")
        == "postgresql://user:pass@db:5432/app"
    )
