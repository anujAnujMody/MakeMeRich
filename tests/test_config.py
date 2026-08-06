from strategies.config import get_config, get_instruments


class TestGetConfig:
    def test_get_config_orbs(self) -> None:
        cfg = get_config("orbs")
        assert cfg is not None
        assert cfg.name == "orbs"
        assert cfg.enabled is True
        assert len(cfg.instruments) == 4

    def test_get_config_unknown(self) -> None:
        assert get_config("nonexistent") is None


class TestGetInstruments:
    def test_instrument_lot_sizes(self) -> None:
        instruments = get_instruments("orbs")
        lot_map = {i.symbol: i.lot_size for i in instruments}
        assert lot_map["BANKNIFTY"] == 15
        assert lot_map["NIFTY"] == 25
        assert lot_map["FINNIFTY"] == 25
        assert lot_map["SENSEX"] == 10
