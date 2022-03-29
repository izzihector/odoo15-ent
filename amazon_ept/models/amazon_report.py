# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

"""
Added class and fields to store the developer details.
"""
import time
from datetime import datetime, timedelta
import pytz
from odoo import models, fields, api, _
from odoo.exceptions import UserError
from odoo.addons.iap.tools import iap_tools
from ..reportTypes import ReportType
from ..endpoint import DEFAULT_ENDPOINT

utc = pytz.utc

AMZ_INSTANCE_EPT = 'amazon.instance.ept'
COMMON_LOG_BOOK_EPT = 'common.log.book.ept'
IR_MODEL = 'ir.model'
COMMON_LOG_LINES_EPT = 'common.log.lines.ept'
DATE_YMDHMS = "%Y-%m-%d %H:%M:%S"
DATE_YMDTHMS = "%Y-%m-%dT%H:%M:%S"
STOCK_MOVE = 'stock.move'
AMZ_SELLER_EPT = 'amazon.seller.ept'
SALE_ORDER = "sale.order"
IR_ACTION_ACT_WINDOW = 'ir.actions.act_window'
RES_PARTNER = 'res.partner'
VIEW_MODE = 'tree,form'


class AmazonReports(models.AbstractModel):
    """
    Added this class to store the developer details
    """
    _name = "amazon.reports"
    _description = 'Amazon Reports'

    def report_start_and_end_date(self):
        """
        Prepare Start and End Date for request reports
        @author: Keyur Kanani
        :return: start_date, end_date
        """
        start_date, end_date = self.start_date, self.end_date
        if start_date:
            try:
                db_import_time = time.strptime(str(start_date), DATE_YMDHMS)
            except Exception:
                db_import_time = time.strptime(str(start_date), "%Y-%m-%d %H:%M:%S.%f")
            db_import_time = time.strftime(DATE_YMDTHMS, db_import_time)
            start_date = time.strftime(DATE_YMDTHMS, time.gmtime(
                time.mktime(time.strptime(db_import_time, DATE_YMDTHMS))))
            start_date = str(start_date) + 'Z'
        else:
            today = datetime.now()
            earlier = today - timedelta(days=30)
            earlier_str = earlier.strftime(DATE_YMDTHMS)
            start_date = earlier_str + 'Z'

        if end_date:
            try:
                db_import_time = time.strptime(str(end_date), DATE_YMDHMS)
            except Exception:
                db_import_time = time.strptime(str(end_date), "%Y-%m-%d %H:%M:%S.%f")
            db_import_time = time.strftime(DATE_YMDTHMS, db_import_time)
            end_date = time.strftime(DATE_YMDTHMS, time.gmtime(
                time.mktime(time.strptime(db_import_time, DATE_YMDTHMS))))
            end_date = str(end_date) + 'Z'
        else:
            today = datetime.now()
            earlier_str = today.strftime(DATE_YMDTHMS)
            end_date = earlier_str + 'Z'

        return start_date, end_date


    def prepare_amazon_request_report_kwargs(self, seller):
        """
        Prepare General Amazon Request dictionary.
        :param seller: amazon.seller.ept()
        :return: {}
        """
        account = self.env['iap.account'].search([('service_name', '=', 'amazon_ept')])
        dbuuid = self.env['ir.config_parameter'].sudo().get_param('database.uuid')
        instances_obj = self.env[AMZ_INSTANCE_EPT]
        instances = instances_obj.search([('seller_id', '=', seller.id)])
        marketplace_ids = tuple(map(lambda x: x.market_place_id, instances))

        return {'merchant_id': seller.merchant_id and str(seller.merchant_id) or False,
                'auth_token': seller.auth_token and str(seller.auth_token) or False,
                'app_name': 'amazon_ept',
                'account_token': account.account_token,
                'dbuuid': dbuuid,
                'amazon_marketplace_code': seller.country_id.amazon_marketplace_code or
                                           seller.country_id.code,
                'marketplaceids': marketplace_ids}

    def request_report(self):
        """
        Request _GET_AMAZON_FULFILLED_SHIPMENTS_DATA_ Report from Amazon for specific date range.
        @author: Keyur Kanani
        :return: Boolean
        """
        seller, report_type = self.seller_id, self.report_type
        if not seller:
            raise UserError(_('Please select Seller'))

        emipro_api = self._context.get('emipro_api', 'request_report_v13')
        kwargs = self.prepare_amazon_request_report_kwargs(seller)
        if self.start_date or self.end_date:
            start_date, end_date = self.report_start_and_end_date()
            kwargs.update({'start_date': start_date, 'end_date': end_date})
        kwargs.update({'emipro_api': emipro_api, 'report_type': report_type})
        response = iap_tools.iap_jsonrpc(DEFAULT_ENDPOINT, params=kwargs, timeout=1000)
        if response.get('reason', False):
            if not self._context.get('is_auto_process', False):
                raise UserError(_(response.get('reason', {})))
            self.amz_search_or_create_logs_ept(response.get('reason', {}))
        else:
            result = response.get('result', {})
            self.update_report_history(result)
        return True

    def get_report_request_list(self):
        """
        Get Report Requests List from Amazon, Check Status of Process.
        :return: Boolean
        """
        self.ensure_one()
        if not self.seller_id:
            raise UserError(_('Please select Seller'))
        if self.report_request_id:
            kwargs = self.prepare_amazon_request_report_kwargs(self.seller_id)
            kwargs.update({'emipro_api': 'get_report_request_list_v13', 'request_ids': (self.report_request_id)})
            response = iap_tools.iap_jsonrpc(DEFAULT_ENDPOINT, params=kwargs, timeout=1000)
            if response.get('reason', False):
                raise UserError(_(response.get('reason', {})))
            list_of_wrapper = response.get('result', {})
            for result in list_of_wrapper:
                self.update_report_history(result)
        return True

    def get_report(self):
        """
        Get Shipment Report as an attachment in reports form view.
        :return: True
        """
        self.ensure_one()
        seller = self.seller_id
        if not seller:
            raise UserError(_('Please select seller'))
        if self.report_id:
            amz_report_type = self._context.get('amz_report_type', False)
            emipro_api = self._context.get('emipro_api', 'get_report_v13')
            kwargs = self.prepare_amazon_request_report_kwargs(self.seller_id)
            kwargs.update({'emipro_api': emipro_api, 'report_id': self.report_id})
            if amz_report_type:
                kwargs.update({'amz_report_type': amz_report_type})
            response = iap_tools.iap_jsonrpc(DEFAULT_ENDPOINT, params=kwargs, timeout=1000)
            if response.get('reason', False):
                if not self._context.get('is_auto_process', False):
                    raise UserError(_(response.get('reason', {})))
                self.amz_search_or_create_logs_ept(response.get('reason', {}))
            else:
                result = response.get('result', '')
                if self.report_type == ReportType.GET_V2_SETTLEMENT_REPORT_DATA_FLAT_FILE_V2:
                    result = response.get('data', {})
                self.create_amazon_report_attachment(result)
        return True

    def get_report_list(self):
        """
        Call Get report list api from amazon
        :return:
        """
        self.ensure_one()
        list_of_wrapper = []
        if not self.seller_id:
            raise UserError(_('Please select seller'))

        kwargs = self.prepare_amazon_request_report_kwargs(self.seller_id)
        kwargs.update({'emipro_api': 'get_report_list_v13', 'request_id': [self.request_id]})
        if self.request_id:
            response = iap_tools.iap_jsonrpc(DEFAULT_ENDPOINT, params=kwargs, timeout=1000)
            if response.get('reason', False):
                if not self._context.get('is_auto_process', False):
                    raise UserError(_(response.get('reason', {})))
                self.amz_search_or_create_logs_ept(response.get('reason', {}))
            else:
                list_of_wrapper = response.get('result', {})

            for result in list_of_wrapper:
                self.update_report_history(result)
        return True

    def update_report_history(self, request_result):
        """
        Update Report History in odoo
        :param request_result:
        :return:
        """
        report_info = request_result.get('ReportInfo', {})
        report_request_info = request_result.get('ReportRequestInfo', {})
        request_id = report_state = report_id = False
        if report_request_info:
            request_id = str(report_request_info.get('ReportRequestId', {}).get('value', ''))
            report_state = report_request_info.get('ReportProcessingStatus', {}).get('value',
                                                                                     '_SUBMITTED_')
            report_id = report_request_info.get('GeneratedReportId', {}).get('value', False)
        elif report_info:
            report_id = report_info.get('ReportId', {}).get('value', False)
            request_id = report_info.get('ReportRequestId', {}).get('value', False)

        if report_state == '_DONE_' and not report_id:
            self.get_report_list()
        report_values = {}
        if not self.report_request_id and request_id:
            report_values.update({'report_request_id': request_id})
        if report_state:
            report_values.update({'state': report_state})
        if report_id:
            report_values.update({'report_id': report_id})
        self.write(report_values)
        return True

    def amz_search_or_create_logs_ept(self, message):
        """
        Download Report from Attachment
        :return: boolean
        """
        common_log_book_obj = self.env[COMMON_LOG_BOOK_EPT]
        common_log_line_obj = self.env[COMMON_LOG_LINES_EPT]
        model_name = self._table.replace('_', '.')
        model_id = common_log_line_obj.get_model_id(model_name)
        log_rec = common_log_book_obj.amazon_search_or_create_transaction_log('import', model_id, self.id)
        if log_rec and log_rec.log_lines:
            log_rec.log_lines.unlink()
        if message:
            common_log_line_obj.create_log_lines(self, message, model_id, self, log_rec)
        return log_rec

    def download_report(self):
        """
        Download Report from Attachment
        :return: boolean
        """
        self.ensure_one()
        if self.attachment_id:
            return {
                'type': 'ir.actions.act_url',
                'url': '/web/content/%s?download=true' % (self.attachment_id.id),
                'target': 'self',
            }
        return True
