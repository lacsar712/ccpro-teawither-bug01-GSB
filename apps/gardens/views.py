from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.template.loader import render_to_string
from django.urls import reverse_lazy
from django.views.generic import (
    CreateView,
    DeleteView,
    ListView,
    UpdateView,
)

from .forms import GardenForm, TroughForm, WitherBatchForm
from .models import Garden, Trough, WitherBatch


def _ready_hint_for_trough(trough):
    """列表「资格提示」列：唯一口径来自模型 ready_block_reason()。"""
    reason = trough.ready_block_reason()
    if not reason:
        return "可下槽"
    return f"不可下槽（{reason}）"


def _wants_htmx(request):
    return request.headers.get("HX-Request") == "true"


def _post_batch_save_message(request, batch):
    """批次保存后的资格提示，口径唯一来自模型 ready_block_reason()。

    模型层已在批次保存时自动复核相关槽位（失格会把「可下槽」退回「萎凋中」），
    这里只负责把同一结论反馈给操作员。
    """
    trough = Trough.objects.get(pk=batch.trough_id)
    demoted = getattr(batch, "_demoted", [])
    if demoted:
        demoted_ids = {pk for pk, _ in demoted}
        for pk, reason in demoted:
            t = Trough.objects.get(pk=pk)
            messages.warning(
                request,
                f"批次已保存；槽位 {t} 已不满足可下槽资格（{reason}），"
                "状态已自动退回「萎凋中」。",
            )
        if trough.pk not in demoted_ids:
            reason = trough.ready_block_reason()
            if reason:
                messages.info(
                    request,
                    f"槽位 {trough} 当前不可下槽：{reason}。",
                )
            else:
                messages.success(
                    request,
                    f"槽位 {trough} 满足可下槽资格，可在槽位编辑页置为「可下槽」。",
                )
        return
    reason = trough.ready_block_reason()
    if reason:
        messages.info(
            request,
            f"批次已保存；槽位 {trough} 当前不可下槽：{reason}。",
        )
    else:
        latest = trough.latest_batch()
        messages.success(
            request,
            f"批次已保存；槽位 {trough} 满足可下槽资格"
            f"（最新批次实测 {latest.actualMoisture}% ≤ "
            f"{Trough.READY_MOISTURE_LIMIT}%），可在槽位编辑页置为「可下槽」。",
        )


@login_required
def home(request):
    # 首页「可下槽」数与槽位列表「可下槽」筛选共用同一 QuerySet，
    # 保证两处数字必然对账。
    ready_troughs = Trough.objects.ready_troughs()
    context = {
        "garden_count": Garden.objects.count(),
        "trough_count": Trough.objects.count(),
        "batch_count": WitherBatch.objects.count(),
        "ready_count": ready_troughs.count(),
        "withering_count": Trough.objects.filter(
            status=Trough.STATUS_WITHERING
        ).count(),
        "loading_count": Trough.objects.filter(
            status=Trough.STATUS_LOADING
        ).count(),
        "ready_hint_note": (
            f"资格口径：最新批次实测含水率已填且 ≤ "
            f"{Trough.READY_MOISTURE_LIMIT}% 的槽位方可置为「可下槽」；"
            "首页数量与槽位列表「可下槽」筛选一致。"
        ),
    }
    return render(request, "home.html", context)


# ---- Garden ----


class GardenListView(LoginRequiredMixin, ListView):
    model = Garden
    template_name = "gardens/list.html"
    context_object_name = "gardens"

    def get(self, request, *args, **kwargs):
        self.object_list = self.get_queryset()
        if _wants_htmx(request):
            html = render_to_string(
                "gardens/_table.html",
                {"gardens": self.object_list},
                request=request,
            )
            return HttpResponse(html)
        return super().get(request, *args, **kwargs)


class GardenCreateView(LoginRequiredMixin, CreateView):
    model = Garden
    form_class = GardenForm
    template_name = "gardens/form.html"
    success_url = reverse_lazy("garden_list")

    def form_valid(self, form):
        messages.success(self.request, "茶园已创建")
        response = super().form_valid(form)
        if _wants_htmx(self.request):
            return redirect("garden_list")
        return response


class GardenUpdateView(LoginRequiredMixin, UpdateView):
    model = Garden
    form_class = GardenForm
    template_name = "gardens/form.html"
    success_url = reverse_lazy("garden_list")

    def form_valid(self, form):
        messages.success(self.request, "茶园已更新")
        return super().form_valid(form)


class GardenDeleteView(LoginRequiredMixin, DeleteView):
    model = Garden
    template_name = "gardens/confirm_delete.html"
    success_url = reverse_lazy("garden_list")

    def form_valid(self, form):
        messages.success(self.request, "茶园已删除")
        return super().form_valid(form)


# ---- Trough ----


class TroughListView(LoginRequiredMixin, ListView):
    model = Trough
    template_name = "troughs/list.html"
    context_object_name = "troughs"

    def get_queryset(self):
        qs = Trough.objects.select_related("garden")
        # 「可下槽」筛选与首页计数共用同一 QuerySet（ready_troughs），
        # 筛出的条数必须等于首页「可下槽」数。
        self.filter_ready = self.request.GET.get("ready") == "1"
        if self.filter_ready:
            qs = qs.ready_troughs()
        return qs

    def _annotate_hints(self, troughs):
        for t in troughs:
            t.ready_hint = _ready_hint_for_trough(t)

    def get(self, request, *args, **kwargs):
        self.object_list = self.get_queryset()
        self._annotate_hints(self.object_list)
        if _wants_htmx(request):
            html = render_to_string(
                "troughs/_table.html",
                {"troughs": self.object_list},
                request=request,
            )
            return HttpResponse(html)
        return self.render_to_response(
            self.get_context_data(
                object_list=self.object_list,
                filter_ready=self.filter_ready,
            )
        )


class TroughCreateView(LoginRequiredMixin, CreateView):
    model = Trough
    form_class = TroughForm
    template_name = "troughs/form.html"
    success_url = reverse_lazy("trough_list")

    def form_valid(self, form):
        messages.success(self.request, "萎凋槽已创建")
        return super().form_valid(form)


class TroughUpdateView(LoginRequiredMixin, UpdateView):
    model = Trough
    form_class = TroughForm
    template_name = "troughs/form.html"
    success_url = reverse_lazy("trough_list")

    def form_valid(self, form):
        messages.success(self.request, "萎凋槽已更新")
        return super().form_valid(form)


class TroughDeleteView(LoginRequiredMixin, DeleteView):
    model = Trough
    template_name = "troughs/confirm_delete.html"
    success_url = reverse_lazy("trough_list")

    def form_valid(self, form):
        messages.success(self.request, "萎凋槽已删除")
        return super().form_valid(form)


# ---- WitherBatch ----


class BatchListView(LoginRequiredMixin, ListView):
    model = WitherBatch
    template_name = "batches/list.html"
    context_object_name = "batches"

    def get_queryset(self):
        return WitherBatch.objects.select_related("trough", "trough__garden").all()

    def get(self, request, *args, **kwargs):
        self.object_list = self.get_queryset()
        if _wants_htmx(request):
            html = render_to_string(
                "batches/_table.html",
                {"batches": self.object_list},
                request=request,
            )
            return HttpResponse(html)
        return super().get(request, *args, **kwargs)


class BatchCreateView(LoginRequiredMixin, CreateView):
    model = WitherBatch
    form_class = WitherBatchForm
    template_name = "batches/form.html"
    success_url = reverse_lazy("batch_list")

    def form_valid(self, form):
        response = super().form_valid(form)
        _post_batch_save_message(self.request, self.object)
        return response


class BatchUpdateView(LoginRequiredMixin, UpdateView):
    model = WitherBatch
    form_class = WitherBatchForm
    template_name = "batches/form.html"
    success_url = reverse_lazy("batch_list")

    def form_valid(self, form):
        response = super().form_valid(form)
        _post_batch_save_message(self.request, self.object)
        return response


class BatchDeleteView(LoginRequiredMixin, DeleteView):
    model = WitherBatch
    template_name = "batches/confirm_delete.html"
    success_url = reverse_lazy("batch_list")

    def form_valid(self, form):
        batch = self.object
        trough_id = batch.trough_id
        was_ready = Trough.objects.filter(
            pk=trough_id, status=Trough.STATUS_READY
        ).exists()
        messages.success(self.request, "萎凋批次已删除")
        response = super().form_valid(form)
        # 模型层删除钩子已按同一口径复核；仅在确实引发降级时补充提示。
        trough = Trough.objects.filter(pk=trough_id).first()
        if trough is not None and was_ready and trough.status != Trough.STATUS_READY:
            messages.warning(
                self.request,
                f"槽位 {trough} 已不满足可下槽资格"
                f"（{trough.ready_block_reason()}），状态已自动退回「萎凋中」。",
            )
        return response
