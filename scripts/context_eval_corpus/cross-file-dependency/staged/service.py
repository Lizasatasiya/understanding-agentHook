from mailer import send_email


def notify_customer(customer_email, message):
    send_email(customer_email, "Notification", message)


def send_invoice(invoice, customer_email):
    body = f"Invoice {invoice['number']} due {invoice['due_date']}"
    return send_email(customer_email, f"Invoice {invoice['number']}", body)
