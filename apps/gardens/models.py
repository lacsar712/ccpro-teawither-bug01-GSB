from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import OuterRef, Subquery


class Garden(models.Model):
    name = models.CharField("茶园名称", max_length=120)
    altitudeBand = models.CharField("海拔带", max_length=60)
    notes = models.TextField("备注", blank=True, default="")

    class Meta:
        ordering = ["name"]
        verbose_name = "茶园"
        verbose_name_plural = "茶园"

    def __str__(self):
        return self.name


class TroughQuerySet(models.QuerySet):
    def ready_eligible(self):
        """满足可下槽资格的槽位，口径必须与 Trough.ready_block_reason() 一致：

        最新批次（按 startedAt、id 倒序）存在、实测含水率非空且 ≤ 上限。
        无批次或最新批次实测为空时，子查询结果为 NULL，一并排除。
        """
        latest_actual = Subquery(
            WitherBatch.objects.filter(trough=OuterRef("pk"))
            .order_by("-startedAt", "-id")
            .values("actualMoisture")[:1]
        )
        return (
            self.annotate(_latest_actual_moisture=latest_actual)
            .exclude(_latest_actual_moisture__isnull=True)
            .filter(_latest_actual_moisture__lte=Trough.READY_MOISTURE_LIMIT)
        )

    def ready_troughs(self):
        """当前真正可下槽的槽位：状态已置为可下槽且资格合格。

        首页计数与列表「可下槽」筛选共用本方法，保证两处数字必然对账。
        """
        return self.filter(status=Trough.STATUS_READY).ready_eligible()

    def enforce_ready_eligibility(self, trough_ids):
        """批次增删改后复核受影响槽位，维持不变量：
        状态为「可下槽」的槽位必须满足资格。

        失格的可下槽槽位自动退回「萎凋中」，返回 [(槽位id, 失格原因)]，
        供视图层按同一口径提示。放在模型层是为了覆盖页面、admin、
        管理命令等所有入口，避免再有「其它入口」说法不一致。
        """
        demoted = []
        for trough in self.filter(pk__in=trough_ids, status=Trough.STATUS_READY):
            reason = trough.ready_block_reason()
            if reason:
                trough.status = Trough.STATUS_WITHERING
                trough.save()
                demoted.append((trough.pk, reason))
        return demoted


class Trough(models.Model):
    STATUS_LOADING = "loading"
    STATUS_WITHERING = "withering"
    STATUS_READY = "ready"
    STATUS_CHOICES = [
        (STATUS_LOADING, "装叶中"),
        (STATUS_WITHERING, "萎凋中"),
        (STATUS_READY, "可下槽"),
    ]

    # 可下槽资格唯一口径：最新萎凋批次的实测含水率须已填写且不超过该上限。
    # 模型保存校验、表单、列表资格提示/筛选、首页计数、批次保存后的提示
    # 均通过 ready_block_reason()/is_ready_eligible()/ready_eligible()
    # 使用本常量，不得另立门槛。
    READY_MOISTURE_LIMIT = 40

    objects = TroughQuerySet.as_manager()

    garden = models.ForeignKey(
        Garden,
        on_delete=models.CASCADE,
        related_name="troughs",
        verbose_name="茶园",
    )
    troughCode = models.CharField("槽位编号", max_length=40)
    cultivar = models.CharField("茶树品种", max_length=80)
    loadKg = models.DecimalField("装叶量(kg)", max_digits=10, decimal_places=2)
    status = models.CharField(
        "状态",
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_LOADING,
    )

    class Meta:
        ordering = ["garden__name", "troughCode"]
        verbose_name = "萎凋槽"
        verbose_name_plural = "萎凋槽"
        constraints = [
            models.UniqueConstraint(
                fields=["garden", "troughCode"],
                name="uniq_trough_code_per_garden",
            ),
        ]

    def __str__(self):
        return f"{self.garden.name}-{self.troughCode}"

    def latest_batch(self):
        return self.batches.order_by("-startedAt", "-id").first()

    def ready_block_reason(self):
        """唯一的可下槽资格判定。

        满足资格（最新批次存在、实测含水率已填且 ≤ READY_MOISTURE_LIMIT）
        时返回空字符串；否则返回不允许置为/保持「可下槽」的中文原因。
        模型校验、表单、列表提示/筛选、首页计数与批次保存后的提示都只能
        调用本方法 / is_ready_eligible()，不得另行判定。
        """
        latest = self.latest_batch() if self.pk else None
        if latest is None:
            return "尚无萎凋批次"
        if latest.actualMoisture is None:
            return "最新批次实测含水率未填写"
        if latest.actualMoisture > self.READY_MOISTURE_LIMIT:
            return (
                f"最新批次实测含水率 {latest.actualMoisture}% 高于上限 "
                f"{self.READY_MOISTURE_LIMIT}%"
            )
        return ""

    def is_ready_eligible(self):
        return not self.ready_block_reason()

    def clean(self):
        super().clean()
        if self.status == self.STATUS_READY:
            reason = self.ready_block_reason()
            if reason:
                raise ValidationError(
                    {"status": f"无法设为可下槽：{reason}。"}
                )

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class WitherBatch(models.Model):
    trough = models.ForeignKey(
        Trough,
        on_delete=models.CASCADE,
        related_name="batches",
        verbose_name="萎凋槽",
    )
    startedAt = models.DateTimeField("开始时间")
    targetMoisture = models.DecimalField(
        "目标含水率(%)", max_digits=5, decimal_places=2
    )
    actualMoisture = models.DecimalField(
        "实测含水率(%)",
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
    )
    rollGrade = models.CharField("揉捻等级", max_length=40)

    class Meta:
        ordering = ["-startedAt", "-id"]
        verbose_name = "萎凋批次"
        verbose_name_plural = "萎凋批次"

    def __str__(self):
        return f"{self.trough} @ {self.startedAt:%Y-%m-%d %H:%M}"

    def save(self, *args, **kwargs):
        old_trough_id = None
        if self.pk:
            old_trough_id = (
                WitherBatch.objects.filter(pk=self.pk)
                .values_list("trough_id", flat=True)
                .first()
            )
        super().save(*args, **kwargs)
        # 批次写入（含实测含水变更或改派槽位）可能改变相关槽位的最新批次，
        # 原为「可下槽」但已失格的槽位统一在模型层退回「萎凋中」。
        affected = {self.trough_id}
        if old_trough_id is not None:
            affected.add(old_trough_id)
        self._demoted = Trough.objects.enforce_ready_eligibility(affected)

    def delete(self, *args, **kwargs):
        trough_id = self.trough_id
        result = super().delete(*args, **kwargs)
        # 删除后最新批次可能变化，按同一口径复核。
        Trough.objects.enforce_ready_eligibility([trough_id])
        return result
