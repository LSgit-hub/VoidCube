from VoidCube_app.turn_queue import TurnInputRoute


def test_turn_input_route_only_represents_queued_work() -> None:
    assert list(TurnInputRoute) == [TurnInputRoute.NEXT_TURN]
