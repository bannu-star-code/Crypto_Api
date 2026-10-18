from django.conf import settings


def authentication_options(request):
    return {"allow_signups": settings.ALLOW_SIGNUPS}
