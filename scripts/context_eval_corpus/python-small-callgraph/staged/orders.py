def apply_discount(subtotal, rate):
    return subtotal - subtotal * rate


def compute_order_total(order):
    subtotal = sum(item["price"] for item in order["items"])
    discount_rate = order.get("discount", 0.0)
    return apply_discount(subtotal, discount_rate)
