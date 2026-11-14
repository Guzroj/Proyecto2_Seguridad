from allauth.account.adapter import DefaultAccountAdapter


class AdaptadorCuentas(DefaultAccountAdapter):
    """Sin registro público: las cuentas las crea AC (C15)."""

    def is_open_for_signup(self, request) -> bool:
        return False
