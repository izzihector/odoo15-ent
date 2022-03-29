# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

"""
Added class to get the  amazon live stock report and prepare sellable and unsellable
inventory and process that and added custom fields to store the seller, instance and
inventory information in created inventory report record
"""

import base64
import csv
import time
from datetime import datetime, timedelta
from io import StringIO
import pytz
from dateutil import parser
from odoo import models, fields, api, _
from odoo.addons.iap.tools import iap_tools
from odoo.exceptions import UserError
from ..endpoint import DEFAULT_ENDPOINT
from ..reportTypes import ReportType
FBA_LIVE_STOCK_REPORT = 'amazon.fba.live.stock.report.ept'

utc = pytz.utc

STOCK_QUANT = 'stock.quant'
AMAZON_SELLER_EPT = 'amazon.seller.ept'
AMAZON_INSTANCE_EPT = 'amazon.instance.ept'
IR_MODEL = 'ir.model'
COMMON_LOG_BOOK_EPT = 'common.log.book.ept'
DATE_YMDHMS = "%Y-%m-%d %H:%M:%S"
DATE_YMDTHMS = "%Y-%m-%dT%H:%M:%S"


class AmazonLiveStockReportEpt(models.Model):
    """
    Class used to get the inventory report and process for
    manage sellable and unsellable inventory.
    """
    _name = "amazon.fba.live.stock.report.ept"
    _description = "Amazon Live Stock Report"
    _inherit = ['mail.thread', 'amazon.reports']
    _order = 'id desc'

    @api.depends('seller_id')
    def _compute_company(self):
        for record in self:
            company_id = record.seller_id.company_id.id if record.seller_id else False
            if not company_id:
                company_id = self.env.company.id
            record.company_id = company_id

    @api.model
    def create(self, vals):
        """
        used to set the report name
        """
        sequence = self.env.ref('amazon_ept.seq_import_live_stock_report_job', raise_if_not_found=False)
        report_name = sequence.next_by_id() if sequence else '/'
        vals.update({'name': report_name})
        return super(AmazonLiveStockReportEpt, self).create(vals)

    def list_of_inventory(self):
        """
        This method will display the list of inventory records
        """
        action = {
            'domain': "[('id', 'in', " + str(self.quant_ids.ids) + " )]",
            'name': 'FBA Live Stock Inventory',
            'view_mode': 'tree,form',
            'res_model': STOCK_QUANT,
            'type': 'ir.actions.act_window',
        }
        return action

    def _compute_inventory_count(self):
        """
        This method will count the number of inventory records
        """
        for record in self:
            record.inventory_count = len(record.quant_ids.ids)

    name = fields.Char(size=256)
    state = fields.Selection([('draft', 'Draft'), ('_SUBMITTED_', 'SUBMITTED'), ('_IN_PROGRESS_', 'IN_PROGRESS'),
                              ('_CANCELLED_', 'CANCELLED'), ('_DONE_', 'DONE'), ('_DONE_NO_DATA_', 'DONE_NO_DATA'),
                              ('processed', 'PROCESSED')], string='Report Status', default='draft',
                             help="This Field relocates state of fba live inventory process.")
    seller_id = fields.Many2one(AMAZON_SELLER_EPT, string='Seller', copy=False,
                                help="Select Seller id from you wanted to get Shipping report")
    attachment_id = fields.Many2one('ir.attachment', string="Attachment", help="This Field relocates attachment id.")
    report_id = fields.Char('Report ID', readonly=True, help="This Field relocates report id.")
    report_type = fields.Char(size=256, help='This Field relocates report type.')
    report_request_id = fields.Char('Report Request ID', readonly=True,
                                    help="This Field relocates report request id of amazon.")
    start_date = fields.Datetime(help="Report Start Date")
    end_date = fields.Datetime(help="Report End Date")
    requested_date = fields.Datetime(default=fields.Datetime.now, help="Report Requested Date")
    report_date = fields.Date()
    quant_ids = fields.One2many(STOCK_QUANT, 'fba_live_stock_report_id',
                                string='Inventory', help="This Field relocates inventory ids.")
    user_id = fields.Many2one('res.users', string="Requested User", help="Track which odoo user has requested report")
    company_id = fields.Many2one('res.company', string="Company", copy=False, compute="_compute_company", store=True,
                                 help="This Field relocates amazon company")
    amz_instance_id = fields.Many2one(AMAZON_INSTANCE_EPT, string="Marketplace",
                                      help="This Field relocates amazon instance.")
    amazon_program = fields.Selection(related="seller_id.amazon_program")
    inventory_count = fields.Integer(compute="_compute_inventory_count", help="This Field relocates Inventory count.")
    log_count = fields.Integer(compute="_compute_log_count")

    def unlink(self):
        """
        This Method used to raise error message if trying to delete the processed
        report.
        """
        for report in self:
            if report.state == 'processed':
                raise UserError(_('You cannot delete processed report.'))
        return super(AmazonLiveStockReportEpt, self).unlink()

    def list_of_logs(self):
        """ This method is used to display the mismatch log of processed FBA inventory report"""
        model_id = self.env[IR_MODEL]._get(FBA_LIVE_STOCK_REPORT).id
        action = {
            'domain': "[('res_id', '=', " + str(self.id) + " ), ('model_id', '=', " + str(model_id) + ")]",
            'name': 'MisMatch Logs',
            'view_type': 'form',
            'view_mode': 'tree,form',
            'res_model': COMMON_LOG_BOOK_EPT,
            'type': 'ir.actions.act_window',
        }
        return action

    def _compute_log_count(self):
        """This method will count the number of report logs"""
        common_log_book_obj = self.env[COMMON_LOG_BOOK_EPT]
        model_id = self.env[IR_MODEL]._get(FBA_LIVE_STOCK_REPORT).id
        self.log_count = common_log_book_obj.search_count([('model_id', '=', model_id), ('res_id', '=', self.id)])

    def create_amazon_report_attachment(self, result):
        """
        Get FBA Live Inventory Report as an Attachment of the report
        """
        result = result.encode()
        result = base64.b64encode(result)
        file_name = "Fba_Live_report_" + time.strftime("%Y_%m_%d_%H%M%S") + '.csv'
        attachment = self.env['ir.attachment'].create({
            'name': file_name,
            'datas': result,
            'res_model': 'mail.compose.message',
            'type': 'binary'
        })
        self.message_post(body=_("<b>Live Inventory Report Downloaded</b>"), attachment_ids=attachment.ids)
        self.write({'attachment_id': attachment.id})

    def get_start_end_date_ept(self, seller):
        """
        Prepare start and end date from seller last report sync date.
        :param seller: amazon.seller.ept()
        :return: start_date, end_date
        """
        if seller.inventory_report_last_sync_on:
            start_date = seller.inventory_report_last_sync_on
            start_date = datetime.strftime(start_date, DATE_YMDHMS)
            start_date = self.get_date_time_strptime_ept(start_date)
            start_date = start_date + timedelta(days=seller.live_inv_adjustment_report_days * -1 or -3)
        else:
            start_date = datetime.strptime(datetime.now() and datetime.now().strftime(DATE_YMDHMS),
                                           DATE_YMDHMS)
            start_date = start_date + timedelta(days=seller.live_inv_adjustment_report_days * -1 or -3)
        date_end = datetime.now()
        date_end = date_end.strftime(DATE_YMDHMS)
        return start_date, date_end

    @api.model
    def auto_import_amazon_fba_live_stock_report(self, args={}):
        """
        Import amazon fba live inventory reports.
        If Non European Seller import report instance wise
        If European Seller:
            [PAN EU, EFN, CEP]: Import report Seller wise
            MCI: Import Report Instance wise
        @author: Keyur Kanani
        :param args:
        :return:
        """
        seller_id = args.get('seller_id', False)
        uk_instance_id = args.get('uk_instance_id', False)
        seller = self.env[AMAZON_SELLER_EPT].browse(seller_id)
        if seller:
            start_date, date_end = self.get_start_end_date_ept(seller)
            if seller.is_another_soft_create_fba_inventory:
                return self.import_fba_live_report_another_software(seller, start_date, date_end, args)
            vals = {'seller_id': seller.id, 'report_type': ReportType.GET_FBA_MYI_UNSUPPRESSED_INVENTORY_DATA}
            if seller.amazon_program in ('pan_eu', 'cep'):
                if uk_instance_id:
                    vals.update({'amz_instance_id': uk_instance_id})
                    self.create_and_request_fba_live_stock_report(vals)
                else:
                    self.create_and_request_fba_live_stock_report(vals)
            elif not seller.is_european_region and not seller.amz_fba_us_program == 'narf':
                self.import_fba_live_report_for_other_programs(seller, vals, args)
            elif seller.amazon_program == 'efn' or seller.amz_fba_us_program == 'narf':
                vals.update({'start_date': start_date, 'end_date': date_end})
                self.create_and_request_fba_live_stock_report(vals)
            elif seller.amazon_program in ('mci', 'efn+mci'):
                self.import_fba_live_report_for_mci_efn_mci(vals, start_date, date_end, seller, args)
        return True

    def import_fba_live_report_for_other_programs(self, seller, vals, args):
        """
        If Seller is not European Region and Not in NARF Program then this method will be called.
        :param seller: amazon.seller.ept()
        :param vals: dict{}
        :param args: dict{}
        :return:
        """
        if args.get('instance_id', False):
            vals.update({'amz_instance_id': args.get('instance_id', False), 'report_date': datetime.now()})
            self.create_and_request_fba_live_stock_report(vals)
        else:
            for instance in seller.instance_ids:
                vals.update({'amz_instance_id': instance.id, 'report_date': datetime.now()})
                self.create_and_request_fba_live_stock_report(vals)

    def import_fba_live_report_another_software(self, seller, start_date, date_end, args):
        """
        The function for get reports while is Another Software create FBA Live reports
        :param seller: amazon.seller.ept()
        :param start_date: datetime
        :param date_end: datetime
        :param args: dict{}
        :return:
        """
        if not start_date or not date_end:
            raise UserError(_('Please select Date Range'))
        vals = {'start_date': start_date, 'end_date': date_end, 'seller_id': seller}
        if args.get('instance_id', False) or args.get('uk_instance_id', False):
            instance_id = self.env[AMAZON_INSTANCE_EPT].browse(args.get('instance_id', False) or
                                                               args.get('uk_instance_id', False))
            vals.update({'us_region_instance_id': instance_id})
        return self.get_inventory_report(vals)

    def import_fba_live_report_for_mci_efn_mci(self, vals, start_date, date_end, seller, args):
        """
        Import FBA Live Inventory Report for MCI and EFN + MCI Programs
        :param vals: dict{}
        :param start_date: datetime
        :param date_end: datetime
        :param seller: amazon.seller.ept()
        :param args: dict{}
        :return:
        """
        if args.get('instance_id', False):
            vals.update({'start_date': start_date, 'end_date': date_end,
                         'amz_instance_id': args.get('instance_id', False)})
            self.create_and_request_fba_live_stock_report(vals)
        else:
            for instance in seller.instance_ids:
                vals.update({'start_date': start_date, 'end_date': date_end,
                             'amz_instance_id': instance.id})
                self.create_and_request_fba_live_stock_report(vals)

    def create_and_request_fba_live_stock_report(self, vals):
        """
        Create and request live stock report
        :param vals:
        :return:
        """
        live_stock_report = self.create(vals)
        live_stock_report.request_report()
        return live_stock_report

    def get_start_end_date_for_inv_reprts(self, vals):
        """
        Get start date and end date for get inventory reports
        :param vals: dict{}
        :return: start_date, end_date
        """
        start_date = vals.get('start_date', '')
        end_date = vals.get('end_date', '')
        seller = vals.get('seller_id', False)
        if start_date and end_date:
            start_date = self.get_date_time_strptime_ept(start_date)
            end_date = self.get_date_time_strptime_ept(end_date)
        elif self.inventory_report_last_sync_on:
            start_date = self.inventory_report_last_sync_on
            start_date = self.get_date_time_strptime_ept(start_date)
            end_date = self.get_date_time_strptime_ept(end_date)
        else:
            start_date = datetime.now() + timedelta(days=seller.live_inv_adjustment_report_days * -1 or -3)
            start_date = self.get_date_time_strptime_ept(start_date)
            date_end = datetime.now()
            end_date = date_end.strftime(DATE_YMDHMS)
        return start_date, end_date

    def get_inventory_report(self, vals):
        """
        This method will process for prepare inventory report ids and
        and return the created inventory records
        param vals: dict - start date, end date and seller
        """
        amazon_process_job_log_obj = self.env[COMMON_LOG_BOOK_EPT]
        model_id = self.env[IR_MODEL]._get(FBA_LIVE_STOCK_REPORT).id
        inv_report_ids = []
        result = {}
        seller = vals.get('seller_id', False)
        instance = vals.get('us_region_instance_id', False)
        start_date, end_date = self.get_start_end_date_for_inv_reprts(vals)
        log_rec = amazon_process_job_log_obj.amazon_create_transaction_log('import', model_id, self.id)
        inv_report_ids = self.prepare_amazon_inventory_report_ids_ept(seller, inv_report_ids, start_date, end_date,
                                                                      log_rec, instance)
        if inv_report_ids:
            action = self.env.ref('amazon_ept.action_live_stock_report_ept', False)
            result = action.read()[0] if action else {}
            if len(inv_report_ids) > 1:
                result['domain'] = "[('id','in',[" + ','.join(map(str, inv_report_ids)) + "])]"
            else:
                res = self.env.ref('amazon_ept.amazon_live_stock_report_form_view_ept', raise_if_not_found=False)
                result['views'] = [(res and res.id or False, 'form')]
                result['res_id'] = inv_report_ids[0] if inv_report_ids else False
        return result

    @staticmethod
    def get_date_time_strptime_ept(date):
        """
        Get Date time object from string
        :param date: Date time
        :return: datetime object
        """
        return datetime.strptime(str(date), DATE_YMDHMS)

    def prepare_amazon_inventory_report_ids_ept(self, seller, inv_report_ids, start_date, end_date, log_rec, instance):
        """
        This method is used to prepare amazon inventory report
        """
        if not (seller.amazon_program in ('pan_eu', 'cep') or not seller.is_european_region) and \
                (seller.amz_fba_us_program == 'narf'):
            start_date = (datetime.today().date() - timedelta(days=1)).strftime('%Y-%m-%d 00:00:00')
            end_date = (datetime.today().date() - timedelta(days=1)).strftime('%Y-%m-%d 23:59:59')
        inv_report_ids = self.get_live_inventory_report(inv_report_ids, start_date, end_date, log_rec, seller,
                                                        seller.amazon_program, instance)
        return inv_report_ids

    def prepare_amz_live_inventory_report_kwargs(self, seller, emipro_api):
        """
        Prepare General Amazon Request dictionary.
        @author: Twinkal Chandarana
        :param seller: amazon.seller.ept()
        :param emipro_api : name of api to request for different amazon operation
        :return: {}
        """
        account = self.env['iap.account'].search([('service_name', '=', 'amazon_ept')])
        dbuuid = self.env['ir.config_parameter'].sudo().get_param('database.uuid')
        return {'merchant_id': seller.merchant_id and str(seller.merchant_id) or False,
                'auth_token': seller.auth_token and str(seller.auth_token) or False,
                'app_name': 'amazon_ept',
                'emipro_api': emipro_api,
                'account_token': account.account_token,
                'dbuuid': dbuuid,
                'amazon_marketplace_code': seller.country_id.amazon_marketplace_code or
                                           seller.country_id.code, }

    def get_live_inventory_report(self, inv_report_ids, start_date, end_date, job, seller, amazon_program, instance_id):
        """
        This method is used to get the live inventory report based on amazon
        program return the inventory record ids and update the
        inventory_report_last_sync_on.
        """
        list_of_wrapper = []
        instances = instance_id if instance_id  else seller.instance_ids
        con_start_date, con_end_date = self.report_start_and_end_date_cron(start_date, end_date)
        if seller.is_european_region and amazon_program == 'pan_eu' and not instance_id:
            instances = instances.filtered(lambda x: not x.market_place_id == 'A1F83G8C2ARO7P')
        kwargs = self.prepare_amz_live_inventory_report_kwargs(seller,
                                                               'get_shipping_or_inventory_report_by_marketplaces')
        kwargs.update({'start_date': con_start_date,
                       'end_date': con_end_date,
                       'report_type': '_GET_FBA_MYI_UNSUPPRESSED_INVENTORY_DATA_',
                       'marketplaceids': instances.mapped('market_place_id')})
        response = iap_tools.iap_jsonrpc(DEFAULT_ENDPOINT, params=kwargs, timeout=1000)
        if response.get('reason', False):
            if self._context.get('is_auto_process', False):
                job.write({'log_lines': [(0, 0, {'message': response.get('reason', {})})]})
            else:
                raise UserError(_(response.get('reason', {})))
        else:
            list_of_wrapper = response.get('result', {})
        if instance_id:
            inv_report_ids = self.with_context({'amz_instance_id': instances.id}).\
                request_for_amazon_live_inv_report_ids(seller, list_of_wrapper, inv_report_ids)
        else:
            inv_report_ids = self.request_for_amazon_live_inv_report_ids(seller, list_of_wrapper, inv_report_ids)
        if inv_report_ids:
            seller.write({'inventory_report_last_sync_on': end_date})
        return inv_report_ids

    def request_for_amazon_live_inv_report_ids(self, seller, list_of_wrapper, inv_report_ids):
        """
        This method is used to process the result and based on that it will find
        report exist if not than create and return the list of inventory records
        """
        ctx = self._context.copy() or {}
        latest_report = self.prepare_amz_live_inventory_report_data(list_of_wrapper)
        for report in latest_report:
            amz_start_date = parser.parse(str(report.get('StartDate', {}).get('value', ''))).astimezone(
                utc).strftime(DATE_YMDHMS)
            amz_end_date = parser.parse(str(report.get('EndDate', {}).get('value', ''))).astimezone(utc).strftime(
                DATE_YMDHMS)
            submited_date = parser.parse(str(report.get('SubmittedDate', {}).get('value', ''))).astimezone(
                utc).strftime(DATE_YMDHMS)
            report_id = report.get('GeneratedReportId', {}).get('value', '')
            request_id = report.get('ReportRequestId', {}).get('value', '')
            report_type = report.get('ReportType', {}).get('value', '')
            state = report.get('ReportProcessingStatus', {}).get('value', '')
            report_exist = self.search(['|', ('report_request_id', '=', request_id), ('report_id', '=', report_id),
                                        ('report_type', '=', report_type)])
            if report_exist:
                report_exist = report_exist[0]
                inv_report_ids.append(report_exist.id)
                continue
            vals = {
                'report_type': report_type,
                'report_request_id': request_id,
                'report_id': report_id,
                'start_date': amz_start_date,
                'end_date': amz_end_date,
                'requested_date': submited_date,
                'state': state,
                'seller_id': seller.id,
                'user_id': self._uid,
            }
            if ctx.get('amz_instance_id', False):
                instance_id = ctx.get('amz_instance_id', False)
                vals.update({'amz_instance_id': instance_id})
            inv_report_id = self.create(vals)
            inv_report_ids.append(inv_report_id.id)
        return inv_report_ids

    @staticmethod
    def prepare_amz_live_inventory_report_data(list_of_wrapper):
        """
        Prepare Data for result of live inventory reports
        :param list_of_wrapper:
        :return: list of reports
        """
        reports, latest_report = [], []
        for result in list_of_wrapper:
            if not isinstance(result.get('ReportRequestInfo', []), list):
                reports.append(result.get('ReportRequestInfo', []))
            else:
                reports = result.get('ReportRequestInfo', [])
                reports.reverse()
            for report_info in reports:
                if not latest_report and report_info.get('ReportProcessingStatus', {}).get('value', '') == '_DONE_':
                    latest_report.append(report_info)
                elif latest_report and latest_report[0].get('EndDate', {}).get('value', '') < report_info.get(
                        'EndDate', {}).get('value', '') and report_info.get('ReportProcessingStatus', {}).get(
                            'value', '') == '_DONE_':
                    latest_report = [report_info]
        return latest_report

    @staticmethod
    def report_start_and_end_date_cron(start_date, end_date):
        """
        Prepare start date and end Date for request reports
        :return: start_date and end_date
        """
        if start_date:
            db_import_time = time.strptime(str(start_date), DATE_YMDHMS)
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
            db_import_time = time.strptime(str(end_date), DATE_YMDHMS)
            db_import_time = time.strftime(DATE_YMDTHMS, db_import_time)
            end_date = time.strftime(DATE_YMDTHMS, time.gmtime(
                time.mktime(time.strptime(db_import_time, DATE_YMDTHMS))))
            end_date = str(end_date) + 'Z'
        else:
            today = datetime.now()
            earlier_str = today.strftime(DATE_YMDTHMS)
            end_date = earlier_str + 'Z'
        return start_date, end_date

    @api.model
    def auto_process_amazon_fba_live_stock_report(self, args={}):
        """
        This method will process fba live stock report via cron
        """
        seller_id = args.get('seller_id', False)
        seller = self.env[AMAZON_SELLER_EPT].browse(seller_id)
        if seller:
            fba_live_stock_report = self.search([('seller_id', '=', seller.id),
                                                 ('state', 'in', ['_SUBMITTED_', '_IN_PROGRESS_'])])
            fba_live_stock_report.get_report_request_list_via_cron(seller)
            reports = self.search([('seller_id', '=', seller.id), ('report_id', '!=', False),
                                   ('state', 'in', ['_DONE_', '_SUBMITTED_', '_IN_PROGRESS_'])])
            for report in reports:
                if not report.attachment_id:
                    report.get_report()
                report.with_context(is_auto_process=True).process_fba_live_stock_report()
                report.set_fulfillment_channel_sku()
                self._cr.commit()
        return True

    def get_report_request_list_via_cron(self, seller):
        """
        This method will request for get inventory report and process response
        to update report vals
        """
        if not seller:
            raise UserError(_('Please select Seller Id'))
        request_ids = [report.report_request_id for report in self]
        report_info_records = {report.report_request_id: report for report in self}
        if request_ids:
            kwargs = self.prepare_amz_live_inventory_report_kwargs(seller, 'get_report_request_list_v13')
            kwargs.update({'request_ids': request_ids})
            response = iap_tools.iap_jsonrpc(DEFAULT_ENDPOINT, params=kwargs, timeout=1000)
            if response.get('reason', False):
                raise UserError(_(response.get('reason', {})))
            list_of_wrapper = response.get('result', {})
            for result in list_of_wrapper:
                self.update_report_history_via_cron(result, report_info_records)
        return True

    @staticmethod
    def update_report_history_via_cron(request_result, report_info_records):
        """
        This method will process the request result and update the report state and report
        id
        """
        report_request_info = []
        if isinstance(request_result.get('ReportRequestInfo', []), list):
            report_request_info = request_result.get('ReportRequestInfo', [])
        else:
            report_request_info.append(request_result.get('ReportRequestInfo', {}))
        for info in report_request_info:
            request_id = str(info.get('ReportRequestId', {}).get('value', ''))
            report_state = info.get('ReportProcessingStatus', {}).get('value', '_SUBMITTED_')
            report_id = info.get('GeneratedReportId', {}).get('value', False)
            report_record = report_info_records.get(request_id, False)
            vals = {}
            if report_state:
                vals.update({'state': report_state})
            if report_id:
                vals.update({'report_id': report_id})
            if report_record:
                report_record.write(vals)
        return True

    @api.constrains('start_date', 'end_date')
    def _check_duration(self):
        """
        This Method check date duration,
        :return: This Method return Boolean(True/False)
        """
        if self.start_date and self.end_date < self.start_date:
            raise UserError(_('Error!\nThe start date must be precede its end date.'))
        return True

    def prepare_amazon_request_report_kwargs(self, seller):
        """
        Inherited the method because of passing marketplace ids
        :param seller:
        :return:
        """
        amazon_instance = self.env[AMAZON_INSTANCE_EPT]
        kwargs = super(AmazonLiveStockReportEpt, self).prepare_amazon_request_report_kwargs(seller)
        if self.amz_instance_id:
            marketplaceids = self.amz_instance_id.mapped('market_place_id')
        else:
            if seller.amazon_program in ('pan_eu', 'cep'):
                instances = amazon_instance.search([('seller_id', '=', seller.id),
                                                    ('market_place_id', '!=', 'A1F83G8C2ARO7P')])
            else:
                instances = amazon_instance.search([('seller_id', '=', seller.id)])
            marketplaceids = tuple(map(lambda x: x.market_place_id, instances))
        kwargs.update({'marketplace_ids': marketplaceids})
        return kwargs

    def process_fba_live_stock_report(self):
        """
        This Method relocates processed fba live stock reports.
        This Method prepare sellable line dict, unsellable line dict and
            generate inventory based on sellable line dict, unsellable line.
        :return:
        """
        self.ensure_one()
        ir_cron_obj = self.env['ir.cron']
        amazon_process_job_log_obj = self.env[COMMON_LOG_BOOK_EPT]
        common_log_line_obj = self.env['common.log.lines.ept']
        model_id = self.env[IR_MODEL]._get(FBA_LIVE_STOCK_REPORT).id
        if not self._context.get('is_auto_process', False):
            ir_cron_obj.with_context({'raise_warning': True}).find_running_schedulers(
                'ir_cron_process_fba_live_stock_report_seller_', self.seller_id.id)
        if not self.attachment_id:
            raise UserError(_("There is no any report are attached with this record."))
        imp_file = StringIO(base64.decodebytes(self.attachment_id.datas).decode())
        reader = csv.DictReader(imp_file, delimiter='\t')
        job = amazon_process_job_log_obj.search([('model_id', '=', model_id), ('res_id', '=', self.id)])
        if not job:
            message = 'Live Stock Inventory Report Process.'
            job = amazon_process_job_log_obj.amazon_create_transaction_log('import', model_id, self.id)
            common_log_line_obj.create_log_lines(message, model_id, self, job)
        sellable_line_dict, unsellable_line_dict = self.fill_dictionary_from_file_by_instance(reader, job)
        if self.amz_instance_id:
            amz_warehouse = self.amz_instance_id.fba_warehouse_id or False
        else:
            fba_warehouse_ids = self.seller_id.amz_warehouse_ids.filtered(lambda l: l.is_fba_warehouse)
            amz_warehouse = fba_warehouse_ids[0] if fba_warehouse_ids else False
        if amz_warehouse:
            self.create_stock_inventory_from_amazon_live_report(sellable_line_dict, unsellable_line_dict, amz_warehouse,
                                                                job=job)
        self.write({'state': 'processed'})
        self.set_fulfillment_channel_sku()

    def fill_dictionary_from_file_by_instance(self, reader, job):
        """
        This method is used to prepare sellable product qty dict and unsellable product qty dict
        as per the instance selected in report.
        This qty will be passed to create stock inventory adjustment report.
        :param reader: This Arguments relocates report of amazon fba live inventory data.
        :param job: This Arguments relocates job log of amazon fba live inventory.
        :return: This Method prepare and return sellable line dict, unsellable line dict.
        """
        sellable_line_dict = {}
        unsellable_line_dict = {}
        product_obj = self.env['product.product']
        for row in reader:
            seller_sku = row.get('sku', '') or row.get('seller-sku', '')
            afn_listing = row.get('afn-listing-exists', '')
            if afn_listing == '' or not seller_sku:
                continue
            amazon_product = self.process_report_and_find_amazon_product(row)
            odoo_product = amazon_product.product_id if amazon_product else False
            if not odoo_product:
                odoo_product = product_obj.search([('default_code', '=', seller_sku)], limit=1)
                if not odoo_product:
                    message = "Product not found for seller sku %s" % (seller_sku)
                    job.write({'log_lines': [(0, 0, {'message': message})]})
                    continue
            odoo_product_id = odoo_product.id
            sellable_qty = sellable_line_dict.get(odoo_product_id, 0.0)
            if self.seller_id.amz_is_reserved_qty_included_inventory_report:
                sellable_line_dict.update(
                    {odoo_product_id: sellable_qty + float(row.get('afn-fulfillable-quantity', 0.0)) + float(
                        row.get('afn-reserved-quantity', 0.0))})
            else:
                sellable_line_dict.update(
                    {odoo_product_id: sellable_qty + float(row.get('afn-fulfillable-quantity', 0.0))})
            unsellable_qty = unsellable_line_dict.get(odoo_product_id, 0.0)
            unsellable_line_dict.update({
                odoo_product_id: unsellable_qty + float(row.get('afn-unsellable-quantity', 0.0))})
        return sellable_line_dict, unsellable_line_dict

    def process_report_and_find_amazon_product(self, row):
        """
        This method will process report and find the amazon product and return that
        """
        amazon_product_obj = self.env['amazon.product.ept']
        amazon_instance_obj = self.env[AMAZON_INSTANCE_EPT]
        instance_ids = amazon_instance_obj.search([('seller_id', '=', self.seller_id.id)]).ids
        seller_sku = row.get('sku', '') or row.get('seller-sku', '')
        domain = [('seller_sku', '=', seller_sku), ('fulfillment_by', '=', 'FBA')]
        domain = self.append_instance_in_domain_ept(domain, instance_ids)
        amazon_product = amazon_product_obj.search(domain, limit=1)
        if not amazon_product:
            product_asin = row.get('asin', '')
            domain = [('product_asin', '=', product_asin), ('fulfillment_by', '=', 'FBA')]
            domain = self.append_instance_in_domain_ept(domain, instance_ids)
            amazon_product = amazon_product_obj.search(domain, limit=1)
        return amazon_product

    def append_instance_in_domain_ept(self, domain, instance_ids):
        """
        The method will extend domain of search amazon products.
        :param domain: list of domain
        :param instance_ids: list of amazon instance ids
        :return: updated list of domain
        """
        if self.amz_instance_id:
            domain.append(('instance_id', '=', self.amz_instance_id.id))
        else:
            domain.append(('instance_id', 'in', instance_ids))
        return domain

    def create_stock_inventory_from_amazon_live_report(self, sellable_line_dict, unsellable_line_dict,
                                                       amz_warehouse, job):
        """
        This Method relocates create stock inventory from amazon live report.
        This Method prepare inventory dictionary and create inventory.
        :param sellable_line_dict: This Arguments relocates sellable line dict.
        :param unsellable_line_dict: This Arguments relocates unsellable line dict.
        :param amz_warehouse:  This Arguments relocates amazon warehouse based on seller.
        :param job: This Arguments relocates job log of amazon fba live inventory.
        :return: This Method return boolean (True/False).
        """
        stock_quant = self.env[STOCK_QUANT]
        auto_validate = self.seller_id.validate_stock_inventory_for_report
        if sellable_line_dict:
            amazon_warehouse_location = amz_warehouse.lot_stock_id
            stock_quant.with_context(fba_live_inv_id=self.id).create_inventory_adjustment_ept(
                sellable_line_dict, amazon_warehouse_location, auto_apply=auto_validate, name=self.name)
        if not amz_warehouse.unsellable_location_id:
            message = 'unsellable location not found for warehouse %s.' % (amz_warehouse.name)
            job.write({'log_lines': [(0, 0, {'message': message})]})
        else:
            if unsellable_line_dict:
                amazon_warehouse_location = amz_warehouse.unsellable_location_id
                stock_quant.with_context(fba_live_inv_id=self.id).create_inventory_adjustment_ept(
                    unsellable_line_dict, amazon_warehouse_location, auto_apply=auto_validate, name=self.name)
        if not job.log_lines:
            job.unlink()
        else:
            message = 'Inventory adjustment process has been completed open log to view products' \
                      'which are not processed due to any reason.'
            job.write({'log_lines': [(0, 0, {'message': message})]})
        return True

    def set_fulfillment_channel_sku(self):
        """
        This Method relocates set fulfillment channel sku.
        :return: This Method return boolean(True/False).
        """
        self.ensure_one()
        if not self.attachment_id:
            raise UserError(_("There is no any report are attached with this record."))
        amazon_product_ept_obj = self.env['amazon.product.ept']
        imp_file = StringIO(base64.decodebytes(self.attachment_id.datas).decode())
        reader = csv.DictReader(imp_file, delimiter='\t')
        for row in reader:
            seller_sku = row.get('sku', False)
            fulfillment_channel_sku = row.get('fnsku', False)
            amazon_product = amazon_product_ept_obj.search([('seller_sku', '=', seller_sku),
                                                            ('fulfillment_by', '=', 'FBA')])
            for product in amazon_product:
                if not product.fulfillment_channel_sku:
                    product.update({'fulfillment_channel_sku': fulfillment_channel_sku})
        return True
