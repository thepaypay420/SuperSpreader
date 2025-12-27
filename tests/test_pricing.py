from utils.pricing import american_to_prob, apply_buffers, decimal_to_prob, prob_to_price


def test_odds_conversions():
    assert round(american_to_prob(+150), 6) == round(100 / 250, 6)
    assert round(american_to_prob(-150), 6) == round(150 / 250, 6)
    assert round(decimal_to_prob(2.5), 6) == round(1 / 2.5, 6)


def test_price_clamp_and_buffers():
    assert prob_to_price(-1.0) == 0.0
    assert prob_to_price(2.0) == 1.0

    # For buys, buffers reduce fair
    p = apply_buffers(0.60, fees_bps=20, slippage_bps=10, latency_bps=5, side="buy")
    assert p < 0.60

    # For sells, buffers increase fair
    p2 = apply_buffers(0.60, fees_bps=20, slippage_bps=10, latency_bps=5, side="sell")
    assert p2 > 0.60
