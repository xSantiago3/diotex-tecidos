def normalize_phone(phone: str) -> str:
    return "".join(char for char in phone if char.isdigit())