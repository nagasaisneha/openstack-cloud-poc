from datetime import datetime, timezone
import json
import logging
from uuid import uuid4

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_http_methods
from django.views.decorators.csrf import csrf_protect

from ..billing import BillingError, BillingErrorCode
from ..poc_metering import collect_project, collector_config, month_bounds
from . import runtime
from .forms import RateForm, StatementForm

LOG = logging.getLogger(__name__)


def period(value=None):
    current = datetime.now(timezone.utc)
    if value is None:
        return month_bounds(current)
    try:
        selected = datetime.strptime(value, '%Y-%m').replace(tzinfo=timezone.utc)
        if selected > current or selected.year < current.year - 5:
            raise ValueError
        return month_bounds(selected)
    except (ValueError, TypeError):
        raise BillingError(BillingErrorCode.INVALID_REQUEST) from None


def failure(error):
    status = {BillingErrorCode.AUTHENTICATION_REQUIRED: 401,
              BillingErrorCode.AUTHORIZATION_DENIED: 403,
              BillingErrorCode.STATEMENT_NOT_FOUND: 404,
              BillingErrorCode.INVALID_REQUEST: 400}.get(error.code, 503)
    response = HttpResponse(str(error), status=status, content_type='text/plain; charset=utf-8')
    response['X-Content-Type-Options'] = 'nosniff'
    return response


def selected_project(request):
    project = request.user.tenant_id
    if any(values.get('project_id', project) != project for values in (request.GET, request.POST)):
        raise BillingError(BillingErrorCode.AUTHORIZATION_DENIED)
    return project


@login_required
@never_cache
@require_GET
def operations(request):
    try:
        actor = runtime.principal(request, system=True)
        runtime.service().available_rate_cards(actor)
        from pathlib import Path
        import os
        import stat
        from ..operations import fresh_status
        path = Path('/var/lib/openstack-poc-operations/status.json')
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o600:
            raise ValueError('unsafe_operations_status')
        data = json.loads(path.read_text())
        fresh = fresh_status(data)
        return render(request, 'poc_billing/operations.html', {'page_title': 'POC Operations', 'status': data,
                      'probes': sorted(data['probes'].items()), 'fresh': fresh}, status=200 if fresh else 503)
    except BillingError as error:
        return failure(error)
    except Exception:
        return HttpResponse('Operations observations are unavailable.', status=503)


@login_required
@never_cache
@csrf_protect
@require_http_methods(['GET', 'POST'])
def project_index(request):
    try:
        actor = runtime.principal(request)
        project = selected_project(request)
        service = runtime.service()
        # Authorize through the billing service before touching usage/history
        # or a collector identity. Caller parameters cannot select another scope.
        statements = service.project_statements(actor, project)
        cards = service.available_rate_cards(actor, project)
        start, end = period(request.POST.get('month') or request.GET.get('month'))
        if request.method == 'POST':
            if request.POST.get('action') != 'refresh':
                raise BillingError(BillingErrorCode.INVALID_REQUEST)
            config = collector_config(settings.POC_BILLING_COLLECTOR_CONFIG)
            batch, inventory = collect_project(config, project, start, end)
            runtime.history().append(batch, inventory)
            messages.success(request, 'Provider usage refreshed for your current project.')
            return redirect(reverse('horizon:project:poc_billing:index') + '?month=' + start.strftime('%Y-%m'))
        batch, inventory = None, None
        try:
            batch, inventory = runtime.history().latest(project, start, end)
            batch = service.source.collect(project, start, end)
        except BillingError as error:
            if error.code != BillingErrorCode.USAGE_SOURCE_UNAVAILABLE:
                raise
        return render(request, 'poc_billing/project.html', {
            'page_title': 'Usage & Billing', 'project_id': project,
            'project_name': request.user.tenant_name, 'month': start.strftime('%Y-%m'),
            'statements': statements, 'cards': cards, 'batch': batch,
            'inventory': inventory, 'source_available': batch is not None,
        })
    except BillingError as error:
        return failure(error)
    except Exception as error:
        LOG.warning('POC project usage unavailable: %s', type(error).__name__)
        return HttpResponse('Usage and billing are temporarily unavailable.', status=503)


@login_required
@never_cache
@csrf_protect
@require_http_methods(['GET', 'POST'])
def admin_index(request):
    try:
        actor = runtime.principal(request, system=True)
        service = runtime.service()
        # Explicit system cloud_admin, not the generic Horizon admin flag.
        cards = service.available_rate_cards(actor)
        projects = runtime.project_choices(request)
        rate_form = RateForm(initial={'currency': 'INR', 'effective_from': datetime.now(timezone.utc).date().replace(day=1)})
        statement_form = StatementForm(projects=projects, cards=cards,
                                        initial={'month': datetime.now(timezone.utc).strftime('%Y-%m')})
        if request.method == 'POST':
            action = request.POST.get('action')
            if action == 'rate':
                rate_form = RateForm(request.POST)
                if rate_form.is_valid():
                    service.configure_rate_card(actor, rate_form.rate_card(), correlation_id=str(uuid4()))
                    messages.success(request, 'Demo rate version saved. Existing statements are unchanged.')
                    return redirect('horizon:admin:poc_billing:index')
            elif action == 'statement':
                statement_form = StatementForm(request.POST, projects=projects, cards=cards)
                if statement_form.is_valid():
                    data = statement_form.cleaned_data
                    start, end = ((data['period_start'], data['period_end']) if data.get('period_start')
                                  else period(data['month']))
                    config = collector_config(settings.POC_BILLING_COLLECTOR_CONFIG)
                    batch, inventory = collect_project(config, data['project_id'], start, end)
                    runtime.history().append(batch, inventory)
                    statement = service.generate_statement(actor, data['project_id'], start, end,
                                                           data['rate_version'], correlation_id=str(uuid4()))
                    messages.success(request, 'Project statement saved (' + statement.status.value + ').')
                    return redirect('horizon:admin:poc_billing:index')
            else:
                raise BillingError(BillingErrorCode.INVALID_REQUEST)
        return render(request, 'poc_billing/admin.html', {
            'page_title': 'Usage & Billing Administration', 'rate_form': rate_form,
            'statement_form': statement_form, 'cards': cards,
            'statements': service.all_statements(actor), 'events': service.audit_events(actor),
        })
    except BillingError as error:
        return failure(error)
    except Exception as error:
        LOG.warning('POC billing administration unavailable: %s', type(error).__name__)
        return HttpResponse('Billing administration is temporarily unavailable.', status=503)


def download(request, statement_id, format, project_id=None):
    try:
        system = project_id is not None
        actor = runtime.principal(request, system=system)
        project = project_id if system else selected_project(request)
        service = runtime.service()
        statement = service.project_statement(actor, project, str(statement_id))
        if format == 'csv':
            content = service.project_statement_csv(actor, project, str(statement_id))
            response = HttpResponse(content, content_type='text/csv; charset=utf-8')
        elif format == 'json':
            response = JsonResponse(statement.to_wire(), json_dumps_params={'indent': 2})
        else:
            raise BillingError(BillingErrorCode.INVALID_REQUEST)
        response['Content-Disposition'] = 'attachment; filename="poc-statement-' + statement.statement_id + '.' + format + '"'
        response['X-Content-Type-Options'] = 'nosniff'
        return response
    except BillingError as error:
        return failure(error)
    except Exception as error:
        LOG.warning('POC statement download unavailable: %s', type(error).__name__)
        return HttpResponse('Statement download is temporarily unavailable.', status=503)


@login_required
@never_cache
@require_GET
def project_download(request, statement_id, format):
    return download(request, statement_id, format)


@login_required
@never_cache
@require_GET
def admin_download(request, project_id, statement_id, format):
    return download(request, statement_id, format, project_id=project_id)
