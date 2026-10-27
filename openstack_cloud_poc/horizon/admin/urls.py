from django.urls import path
from .. import views

urlpatterns = [
    path('', views.admin_index, name='index'),
    path('statements/<str:project_id>/<uuid:statement_id>/<str:format>/',
         views.admin_download, name='download'),
]
