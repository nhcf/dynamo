    if address.is_loopback or address.is_unspecified:
        raise ValueError("raw_advertise_host must be a non-loopback connectable host")
