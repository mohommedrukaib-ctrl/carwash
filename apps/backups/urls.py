from django.urls import path
from . import views

urlpatterns = [
    path('',                       views.backup_dashboard,     name='backup_dashboard'),
    path('create/',                views.create_backup,        name='backup_create'),
    path('list/',                  views.backup_list,          name='backup_list'),
    path('schedule/',              views.backup_schedule,      name='backup_schedule'),
    path('schedule/save/',         views.backup_schedule_save, name='backup_schedule_save'),
    path('<int:pk>/download/',     views.backup_download,      name='backup_download'),
    path('<int:pk>/delete/',       views.backup_delete,        name='backup_delete'),
    path('<int:pk>/restore/',      views.backup_restore,       name='backup_restore'),
    path('cleanup/',               views.backup_cleanup,       name='backup_cleanup'),
]