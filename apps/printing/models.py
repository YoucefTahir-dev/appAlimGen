from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Q
from django.utils.translation import gettext_lazy as _


class PrinterProfile(models.Model):
    BLUETOOTH = 'bluetooth'
    USB = 'usb'
    NETWORK = 'network'
    WINDOWS = 'windows'
    ANDROID = 'android'
    OTHER = 'other'
    CONNECTION_CHOICES = (
        (BLUETOOTH, _('Bluetooth')),
        (USB, _('USB')),
        (NETWORK, _('Réseau TCP/IP')),
        (WINDOWS, _('Imprimante système Windows')),
        (ANDROID, _('Android Print Service')),
        (OTHER, _('Autre')),
    )
    GENERIC_ESCPOS = 'generic_escpos'
    EPSON_ESCPOS = 'epson_escpos'
    XPRINTER = 'xprinter'
    SUNMI = 'sunmi'
    STAR = 'star'
    POSIFLEX = 'posiflex'
    GP = 'gp'
    CUSTOM = 'custom'
    PROTOCOL_CHOICES = (
        (GENERIC_ESCPOS, _('Generic ESC/POS')),
        (EPSON_ESCPOS, _('Epson compatible ESC/POS')),
        (XPRINTER, _('XPrinter')),
        (SUNMI, _('Sunmi')),
        (STAR, _('Star')),
        (POSIFLEX, _('Posiflex')),
        (GP, _('GP')),
        (CUSTOM, _('Autre protocole')),
    )
    PAPER_CHOICES = ((58, _('58 mm')), (80, _('80 mm')))

    name = models.CharField(_('Nom interne'), max_length=100, unique=True)
    description = models.TextField(_('Description'), blank=True)
    printer_type = models.CharField(_('Type d’imprimante'), max_length=50, default='thermal')
    manufacturer = models.CharField(_('Constructeur'), max_length=100, blank=True)
    model_name = models.CharField(_('Modèle'), max_length=100, blank=True)
    connection_mode = models.CharField(_('Mode de connexion'), max_length=20, choices=CONNECTION_CHOICES)
    local_identifier = models.CharField(
        _('Identifiant local facultatif'), max_length=255, blank=True,
        help_text=_('Préférence non secrète uniquement. La découverte Bluetooth reste locale au terminal.'),
    )
    ip_address = models.GenericIPAddressField(_('Adresse IP'), blank=True, null=True)
    network_port = models.PositiveIntegerField(_('Port réseau'), blank=True, null=True)
    paper_width = models.PositiveSmallIntegerField(_('Largeur papier'), choices=PAPER_CHOICES, default=80)
    protocol = models.CharField(_('Protocole'), max_length=30, choices=PROTOCOL_CHOICES, default=GENERIC_ESCPOS)
    characters_per_line = models.PositiveSmallIntegerField(_('Caractères par ligne'), default=48)
    encoding = models.CharField(_('Encodage'), max_length=50, default='cp858')
    auto_print = models.BooleanField(_('Impression automatique'), default=False)
    is_default = models.BooleanField(_('Imprimante par défaut'), default=False)
    is_active = models.BooleanField(_('Active'), default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ('name',)
        constraints = [
            models.UniqueConstraint(
                fields=('is_default',), condition=Q(is_default=True), name='one_default_printer',
            ),
            models.CheckConstraint(
                condition=Q(paper_width__in=(58, 80)), name='printer_paper_width_supported',
            ),
            models.CheckConstraint(
                condition=Q(characters_per_line__gte=16), name='printer_chars_per_line_gte_16',
            ),
        ]
        permissions = (
            ('test_printerprofile', _('Peut générer un test d’impression')),
        )

    def clean(self):
        super().clean()
        if self.connection_mode == self.NETWORK and (not self.ip_address or not self.network_port):
            raise ValidationError({'ip_address': _('Une adresse IP et un port sont requis pour une imprimante réseau.')})
        if self.paper_width == 58 and self.characters_per_line > 42:
            raise ValidationError({'characters_per_line': _('Une imprimante 58 mm ne peut pas dépasser 42 caractères par ligne.')})

    def validate_constraints(self, exclude=None):
        # The default flag is switched atomically in save(); validating it here
        # would prevent a legitimate replacement of the current default.
        super().validate_constraints(exclude=set(exclude or ()) | {'is_default'})

    @transaction.atomic
    def save(self, *args, **kwargs):
        self.full_clean()
        if self.is_default:
            type(self).objects.select_for_update().filter(is_default=True).exclude(pk=self.pk).update(is_default=False)
        return super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class PrintProfile(models.Model):
    TICKET_58 = 'ticket_58'
    TICKET_80 = 'ticket_80'
    INVOICE_A4 = 'invoice_a4'
    PURCHASE_ORDER = 'purchase_order'
    DELIVERY_NOTE = 'delivery_note'
    PRODUCT_LABEL = 'product_label'
    DOCUMENT_CHOICES = (
        (TICKET_58, _('Ticket 58 mm')),
        (TICKET_80, _('Ticket 80 mm')),
        (INVOICE_A4, _('Facture A4')),
        (PURCHASE_ORDER, _('Bon d’achat')),
        (DELIVERY_NOTE, _('Bon de livraison')),
        (PRODUCT_LABEL, _('Étiquette produit')),
    )
    LANGUAGE_CHOICES = (
        ('fr', _('Français')),
        ('ar', _('Arabe')),
        ('en', _('Anglais')),
        ('bilingual', _('Bilingue français/arabe')),
    )

    name = models.CharField(_('Nom'), max_length=100, unique=True)
    document_type = models.CharField(_('Type de document'), max_length=30, choices=DOCUMENT_CHOICES)
    printer = models.ForeignKey(
        PrinterProfile, on_delete=models.SET_NULL, null=True, blank=True, related_name='print_profiles',
    )
    paper_width = models.PositiveSmallIntegerField(_('Largeur papier'), choices=PrinterProfile.PAPER_CHOICES, null=True, blank=True)
    copies = models.PositiveSmallIntegerField(_('Nombre de copies'), default=1)
    language = models.CharField(_('Langue'), max_length=15, choices=LANGUAGE_CHOICES, default='bilingual')
    is_active = models.BooleanField(_('Actif'), default=True)

    class Meta:
        ordering = ('document_type', 'name')
        constraints = [models.CheckConstraint(condition=Q(copies__gte=1), name='print_profile_copies_gte_1')]

    def __str__(self):
        return self.name


class UserPrinterPreference(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='printer_preference')
    printer = models.ForeignKey(PrinterProfile, on_delete=models.PROTECT, related_name='user_preferences')
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f'{self.user} → {self.printer}'
