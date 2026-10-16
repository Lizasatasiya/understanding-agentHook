from mailer import send_email


def notify_customer(customer_email, message):
    send_email(customer_email, "Notification", message)
