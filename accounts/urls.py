from django.urls import path
from . import views

urlpatterns = [
    path('register/', views.register_view, name='register'),
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    path('profile/', views.profile_view, name='profile'),
    path('edit-profile/', views.edit_profile_view, name='edit_profile'),
    path('education/', views.education_view, name='education'),
    path('education/add/', views.education_add_view, name='education_add'),
    path('education/<int:pk>/edit/', views.education_edit_view, name='education_edit'),
    path('education/<int:pk>/delete/', views.education_delete_view, name='education_delete'),
    path('education/<int:pk>/document/', views.education_document_view, name='education_document'),
    path('nid-image/<int:pk>/', views.nid_image_view, name='nid_image'),
    path('apply-to-teach/', views.apply_to_teach_view, name='apply_to_teach'),
    path('change-password/', views.change_password_view, name='change_password'),
    path('delete-account/', views.delete_account_view, name='delete_account'),
]
