from django.urls import path

from chatbot import views

urlpatterns = [
    path('', views.chat_page, name='chat-page'),
    path('ask/', views.chat_ask, name='chat-ask'),
]
