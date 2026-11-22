from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from .views import home, dashboard_view
from .admin_views import (
    admin_users, admin_user_detail, admin_user_edit,
    admin_user_toggle_active, admin_user_delete,
    admin_bookings, admin_booking_status, admin_booking_delete,
    admin_tutors, admin_tutor_edit, admin_tutor_delete,
    admin_reviews,
    admin_requests, admin_request_decide, admin_request_settle, admin_review_delete,
    admin_chat_log,
    admin_verify_nid, admin_verify_education,
)

urlpatterns = [
    path('', home, name='home'),
    path('dashboard/', dashboard_view, name='dashboard'),
    # Django admin DISABLED — replaced by custom panel
    # path('admin/', admin.site.urls),
    path('accounts/', include('accounts.urls')),
    path('tutors/', include('tutors.urls')),
    path('bookings/', include('bookings.urls')),
    path('reviews/', include('reviews.urls')),
    path('payments/', include('payments.urls')),

    # ── Custom Admin Panel ──────────────────────────────
    # Overview is served at /dashboard/ for admins (see quick_tutor/views.py)
    # path('admin-panel/', admin_overview, name='admin-overview'),
    path('admin-panel/users/',       admin_users,               name='admin-users'),
    path('admin-panel/users/<int:pk>/',        admin_user_detail,         name='admin-user-detail'),
    path('admin-panel/users/<int:pk>/edit/',   admin_user_edit,           name='admin-user-edit'),
    path('admin-panel/users/<int:pk>/toggle/', admin_user_toggle_active,  name='admin-user-toggle'),
    path('admin-panel/users/<int:pk>/delete/', admin_user_delete,         name='admin-user-delete'),
    path('admin-panel/users/<int:pk>/verify-nid/<str:decision>/', admin_verify_nid, name='admin-verify-nid'),
    path('admin-panel/educations/<int:pk>/verify/<str:decision>/', admin_verify_education, name='admin-verify-education'),
    path('admin-panel/bookings/',              admin_bookings,            name='admin-bookings'),
    path('admin-panel/bookings/<int:pk>/status/<str:new_status>/', admin_booking_status, name='admin-booking-status'),
    path('admin-panel/bookings/<int:pk>/delete/', admin_booking_delete,   name='admin-booking-delete'),
    path('admin-panel/tutors/',                admin_tutors,              name='admin-tutors'),
    path('admin-panel/tutors/<int:pk>/edit/',  admin_tutor_edit,          name='admin-tutor-edit'),
    path('admin-panel/tutors/<int:pk>/delete/',admin_tutor_delete,        name='admin-tutor-delete'),
    path('chat/', include('chatbot.urls')),
    path('admin-panel/chat/',                  admin_chat_log,            name='admin-chat-log'),
    path('admin-panel/reviews/',               admin_reviews,             name='admin-reviews'),
    path('admin-panel/requests/',              admin_requests,            name='admin-requests'),
    # `settle` must be routed BEFORE the catch-all <str:decision>, or the
    # decision pattern swallows it and treats "settle" as a verdict.
    path('admin-panel/requests/<int:pk>/settle/', admin_request_settle,   name='admin-request-settle'),
    path('admin-panel/requests/<int:pk>/<str:decision>/', admin_request_decide, name='admin-request-decide'),
    path('admin-panel/reviews/<int:pk>/delete/', admin_review_delete,     name='admin-review-delete'),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
