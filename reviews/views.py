from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from .models import Review
from .serializers import ReviewSerializer
from bookings.models import Booking
from django.db.models import Avg

class ReviewCreateView(APIView):
    def post(self, request):
        if not request.user.is_authenticated:
            return Response({'error': 'Not authenticated'}, status=status.HTTP_401_UNAUTHORIZED)

        booking_id = request.data.get('booking')
        try:
            booking = Booking.objects.get(id=booking_id)
            if booking.student != request.user:
                return Response({'error': 'Only the student can review this booking'}, status=status.HTTP_403_FORBIDDEN)

            serializer = ReviewSerializer(data=request.data)
            if serializer.is_valid():
                serializer.save()
                return Response(serializer.data, status=status.HTTP_201_CREATED)
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        except Booking.DoesNotExist:
            return Response({'error': 'Booking not found'}, status=status.HTTP_404_NOT_FOUND)

class ReviewListView(APIView):
    def get(self, request):
        reviews = Review.objects.all()
        serializer = ReviewSerializer(reviews, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

class ReviewDetailView(APIView):
    def get(self, request, pk):
        try:
            review = Review.objects.get(pk=pk)
            serializer = ReviewSerializer(review)
            return Response(serializer.data, status=status.HTTP_200_OK)
        except Review.DoesNotExist:
            return Response({'error': 'Review not found'}, status=status.HTTP_404_NOT_FOUND)

# (value, label) as strings, so they compare with what the form posts.
RATING_CHOICES = [
    ('5', '5 — Excellent'), ('4', '4 — Very good'), ('3', '3 — Good'),
    ('2', '2 — Fair'), ('1', '1 — Poor'),
]


@login_required(login_url='login')
def review_create_view(request):
    booking_id = request.GET.get('booking')
    booking = get_object_or_404(Booking, id=booking_id)

    # Check if user is the student
    if booking.student != request.user:
        messages.error(request, 'Only the student can review this booking!')
        return redirect('booking-list')

    # Check if review already exists
    if hasattr(booking, 'review'):
        messages.error(request, 'You have already reviewed this booking!')
        return redirect('booking-detail', pk=booking.id)

    # Only completed bookings can be reviewed — otherwise the course-duration
    # gate in booking_complete_view can simply be walked around.
    if booking.status != 'completed':
        messages.error(request, 'You can review a booking once it is completed.')
        return redirect('booking-detail', pk=booking.id)

    if request.method == 'POST':
        comment = (request.POST.get('comment') or '').strip()

        def refuse(problem):
            # Back to the form with what was typed, so the review is not lost.
            messages.error(request, problem)
            return render(request, 'reviews/create.html', {
                'booking': booking,
                'tutor': booking.tutor,
                'rating_choices': RATING_CHOICES,
                'form': {'rating': request.POST.get('rating', ''), 'comment': comment},
            })

        # `choices` is not enforced on save(), so bound the value here — this
        # feeds the tutor's public rating and the tutor-list ordering.
        try:
            rating = int(request.POST.get('rating', ''))
        except (TypeError, ValueError):
            return refuse('Please choose a rating between 1 and 5.')

        if not 1 <= rating <= 5:
            return refuse('Please choose a rating between 1 and 5.')

        if not comment:
            return refuse('Please write a short comment with your review.')

        Review.objects.create(
            booking=booking,
            rating=rating,
            comment=comment
        )

        # Update tutor rating — a tutor may not have a profile yet.
        profile = getattr(booking.tutor, 'tutor_profile', None)
        if profile is not None:
            average = Review.objects.filter(
                booking__tutor=booking.tutor
            ).aggregate(a=Avg('rating'))['a']
            if average is not None:
                profile.rating = round(average, 1)
                profile.save(update_fields=['rating'])

        messages.success(request, 'Review submitted successfully!')
        return redirect('booking-detail', pk=booking.id)

    context = {
        'booking': booking,
        'tutor': booking.tutor,
        'rating_choices': RATING_CHOICES,
    }
    return render(request, 'reviews/create.html', context)

def review_list_view(request):
    reviews = Review.objects.all().order_by('-created_at')
    context = {
        'reviews': reviews,
    }
    return render(request, 'reviews/list.html', context)

def review_detail_view(request, pk):
    review = get_object_or_404(Review, pk=pk)
    context = {
        'review': review,
    }
    return render(request, 'reviews/detail.html', context)
