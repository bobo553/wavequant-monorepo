"""交易观察窗口保留全局行情索引，不把资格外日期补算成交易历史。"""


def observation_window_end(length: int, start_index: int, end_index: int | None) -> int:
    if type(start_index) is not int or not 0 <= start_index < max(length, 1):
        raise ValueError('observation window start must be an available market date')
    if end_index is None:
        return length - 1
    if type(end_index) is not int or not start_index <= end_index < length:
        raise ValueError('observation window end must follow its start inside the market')
    return end_index
