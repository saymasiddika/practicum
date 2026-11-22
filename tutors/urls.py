from django.urls import path

from . import views

urlpatterns = [
    path('', views.tutor_list_view, name='tutor-list'),
    path('<int:pk>/', views.tutor_detail_view, name='tutor-detail'),

    # The profile is who the tutor is.
    path('create-profile/', views.tutor_create_profile_view, name='tutor-create-profile'),
    path('edit-profile/', views.tutor_edit_profile_view, name='edit-profile'),

    # Courses are what they teach. `add` is routed before `<int:pk>` so the
    # word is never read as an id.
    path('courses/', views.course_list_view, name='course-list'),
    path('courses/add/', views.course_add_view, name='course-add'),
    path('courses/<int:pk>/edit/', views.course_edit_view, name='course-edit'),
    path('courses/<int:pk>/delete/', views.course_delete_view, name='course-delete'),
    path('courses/<int:pk>/toggle/', views.course_toggle_view, name='course-toggle'),
]
