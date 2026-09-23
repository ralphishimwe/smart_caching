import logging

from django.core.cache import cache
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework import status

from .models import Post
from .serializers import PostSerializer

logger = logging.getLogger(__name__)


POST_LIST_TTL_SECONDS = 60         
POST_DETAIL_TTL_SECONDS = 600      
MY_DRAFTS_TTL_SECONDS = 120        

POST_LIST_BASE_KEY = "posts:list:published"


def build_post_list_key(query_string: str) -> str:
    """
    Build the cache key for the published-posts list.

    Kept as a single source of truth so PostListView.get() and
    PostListView.post() (invalidation) can never end up with mismatched
    key strings.
    """
    if query_string:
        return f"{POST_LIST_BASE_KEY}:{query_string}"
    return POST_LIST_BASE_KEY


def build_post_detail_key(post_id: int) -> str:
    """Per-post cache key — MUST include post_id or every post collides."""
    return f"posts:detail:{post_id}"


def build_my_drafts_key(user_id: int) -> str:
    """
    Per-user cache key for private draft data.

    SECURITY: user_id must be baked into the key. If this key were the same
    for every request (e.g. just "my-drafts"), the first user to hit the
    endpoint would populate the cache, and every *other* authenticated user
    would then be served THAT user's private drafts until the entry expired.
    That's a cross-account data leak of unpublished content — see
    BrokenDraftsView below for exactly this bug, and docs/ACTIVITY.md for
    the full write-up.
    """
    return f"my-drafts:{user_id}"


# ---------------------------------------------------------------------------
# LEVEL 2 — Shared Cache (Public Data)
# ---------------------------------------------------------------------------

class PostListView(APIView):
    """
    GET  /api/posts/       — Returns all published posts.
    POST /api/posts/       — Creates a new post (authenticated users only).
    """

    def get_permissions(self):
        if self.request.method == "POST":
            return [IsAuthenticated()]
        return [AllowAny()]

    def get(self, request):
        query_string = request.query_params.urlencode()
        cache_key = build_post_list_key(query_string)

        cached = cache.get(cache_key)
        if cached is not None:
            return Response(cached)

        queryset = (
            Post.objects
            .filter(status=Post.STATUS_PUBLISHED)
            .select_related("author")
        )
        payload = PostSerializer(queryset, many=True).data
        cache.set(cache_key, payload, timeout=POST_LIST_TTL_SECONDS)

        return Response(payload)

    def post(self, request):
        serializer = PostSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        serializer.save(author=request.user)
        cache.delete(build_post_list_key(query_string=""))

        return Response(serializer.data, status=status.HTTP_201_CREATED)



class PostDetailView(APIView):
    """
    GET /api/posts/<post_id>/ — Returns a single published post.
    """

    permission_classes = [AllowAny]

    def get(self, request, post_id: int):
        cache_key = build_post_detail_key(post_id)

        cached = cache.get(cache_key)
        if cached is not None:
            return Response(cached)

        try:
            post = (
                Post.objects
                .select_related("author")
                .get(id=post_id, status=Post.STATUS_PUBLISHED)
            )
        except Post.DoesNotExist:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

        payload = PostSerializer(post).data
        cache.set(cache_key, payload, timeout=POST_DETAIL_TTL_SECONDS)

        return Response(payload)


# ---------------------------------------------------------------------------
# LEVEL 3 — User-Isolated Cache (Personal Data)
# ---------------------------------------------------------------------------

class MyDraftsView(APIView):
    """
    GET /api/posts/my-drafts/ — Returns draft posts for the logged-in user only.

    !! SECURITY CRITICAL !!
    This endpoint returns private data. Every student must ensure
    that User A can never see User B's drafts under any circumstances.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        cache_key = build_my_drafts_key(request.user.id)

        cached = cache.get(cache_key)
        if cached is not None:
            return Response(cached)

        drafts = (
            Post.objects
            .filter(author=request.user, status=Post.STATUS_DRAFT)
            .select_related("author")
        )
        payload = PostSerializer(drafts, many=True).data
        cache.set(cache_key, payload, timeout=MY_DRAFTS_TTL_SECONDS)

        return Response(payload)


class BrokenDraftsView(APIView):
    """
    GET /api/posts/broken-drafts/

    This view has a critical security bug.
    Your task: read the code, find the bug, and explain it in the activity sheet.
    DO NOT fix the code here — write your answer in docs/ACTIVITY.md.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        data = cache.get("my-drafts")
        if data is None:
            drafts = Post.objects.filter(
                author=request.user,
                status=Post.STATUS_DRAFT
            ).select_related("author")
            serializer = PostSerializer(drafts, many=True)
            data = serializer.data
            cache.set("my-drafts", data, timeout=120)
        return Response(data)