from django.urls import path
from . import views

app_name = 'core'

urlpatterns = [
    path('', views.index, name='index'),
    path('revisions/', views.revisions_view, name='revisions_list'),
    path('revisions/<int:pk>/', views.revision_detail_view, name='revision_detail'),
    path('replay/', views.replay_view, name='replay'),
    path('about/', views.about_view, name='about'),
    path('healthz/', views.health_check, name='health_check'),
    path('comparisons/example/', views.public_example_view, name='public_example'),
    path('comparisons/example/export/', views.public_example_export_view, name='public_example_export'),
    path('comparisons/<int:pk>/', views.comparison_detail_view, name='comparison_detail'),
    path('comparisons/<int:pk>/export/', views.comparison_export_view, name='comparison_export'),
]
