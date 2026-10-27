from django.urls import path
from .. import views

urlpatterns = [
    path('', views.project_index, name='index'),
    path('statements/<uuid:statement_id>/<str:format>/', views.project_download, name='download'),
]
