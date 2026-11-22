"""Registrations for Django's own admin.

Django's /admin/ is disabled in this project — the real one is the hand-rolled
panel under /admin-panel/ — so these exist only to keep the models registered
and consistent if it is ever switched back on.
"""
from django.contrib import admin

from .models import Course, TutorProfile


@admin.register(TutorProfile)
class TutorProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'rating', 'created_at')
    list_filter = ('rating',)
    search_fields = ('user__username', 'bio')


@admin.register(Course)
class CourseAdmin(admin.ModelAdmin):
    list_display = ('name', 'subject', 'tutor', 'monthly_rate', 'months', 'is_active')
    list_filter = ('subject', 'is_active')
    search_fields = ('name', 'subject', 'tutor__username')
