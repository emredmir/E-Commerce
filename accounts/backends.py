from django.contrib.auth.backends import ModelBackend
from django.contrib.auth import get_user_model
from django.core.validators import validate_email
from django.core.exceptions import ValidationError

User = get_user_model()

class EmailOrPhoneBackend(ModelBackend):
    def authenticate(self, request, username=None, password=None, **kwargs):
        user = None
        try:
            # Gelen kullanıcı adı bir e-posta mı diye kontrol et
            validate_email(username)
            user = User.objects.get(email=username)
        except (ValidationError, User.DoesNotExist):
            try:
                # E-posta değilse, telefon numarası olarak sorgula
                user = User.objects.get(phone_number=username)
            except User.DoesNotExist:
                return None
        
        # Kullanıcı bulunduysa ve şifresi doğruysa kullanıcıyı döndür
        if user and user.check_password(password) and self.user_can_authenticate(user):
            return user
        return None