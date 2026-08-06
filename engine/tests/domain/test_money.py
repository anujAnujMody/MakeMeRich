from decimal import Decimal

from te.domain.money import Paise, paise, rupees


def test_rupees_converts_paise_to_decimal() -> None:
    assert rupees(Paise(650_000)) == Decimal("6500.00")


def test_rupees_handles_sub_rupee_amounts() -> None:
    assert rupees(Paise(65)) == Decimal("0.65")


def test_paise_converts_int_rupees() -> None:
    assert paise(20) == Paise(2000)


def test_paise_converts_decimal_rupees() -> None:
    assert paise(Decimal("100.50")) == Paise(10050)


def test_paise_rounds_half_up_on_sub_paise_input() -> None:
    assert paise(Decimal("1.005")) == Paise(101)
