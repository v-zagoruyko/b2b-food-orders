from django.contrib.auth.base_user import BaseUserManager


class UserManager(BaseUserManager):
    def create_user(self, name, telegram_id=None, **extra_fields):
        """Менеджер точки: без пароля, вход через Telegram."""
        user = self.model(name=name, telegram_id=telegram_id, **extra_fields)
        user.set_unusable_password()
        user.save(using=self._db)
        return user

    def create_staff(self, email, name, password, **extra_fields):
        """Сотрудник производства: вход в админку по email и паролю."""
        extra_fields.setdefault("is_staff", True)
        email = self.normalize_email(email)
        user = self.model(email=email, name=name, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, name, password, **extra_fields):
        extra_fields.setdefault("is_superuser", True)
        return self.create_staff(email, name, password, **extra_fields)
