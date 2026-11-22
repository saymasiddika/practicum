from django.urls import path
from . import views
from . import gateway_views

urlpatterns = [
    path('', views.payment_list_view, name='payment-list'),
    path('<int:pk>/', views.payment_detail_view, name='payment-detail'),
    path('create/', views.payment_create_view, name='payment-create'),
    path('manage/', views.payment_admin_view, name='payment-admin'),
    path('approve/<int:pk>/', views.admin_approve_payment, name='admin_approve_payment'),
    path('reject/<int:pk>/', views.admin_reject_payment, name='admin_reject_payment'),

    # SSLCommerz hosted checkout. The four callbacks are POSTed to by the
    # gateway, so they are CSRF-exempt and verify server-side instead.
    path('gateway/start/',   gateway_views.gateway_start,   name='payment-gateway-start'),
    path('gateway/success/', gateway_views.gateway_success, name='payment-gateway-success'),
    path('gateway/fail/',    gateway_views.gateway_fail,    name='payment-gateway-fail'),
    path('gateway/cancel/',  gateway_views.gateway_cancel,  name='payment-gateway-cancel'),
    path('gateway/ipn/',     gateway_views.gateway_ipn,     name='payment-gateway-ipn'),
]
