from django.urls import path

from . import views


urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("portfolio/", views.portfolio, name="portfolio"),
    path(
        "portfolio/spot/preview/",
        views.spot_trade_preview,
        name="spot_trade_preview",
    ),
    path(
        "portfolio/spot/confirm/",
        views.spot_trade_confirm,
        name="spot_trade_confirm",
    ),
]
