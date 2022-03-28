# -*- coding: utf-8 -*-
# See LICENSE file for full copyright and licensing details.

"""
Added class to create an amazon stock adjustment report history to import stock adjustment report
and process that.
"""

import base64
import csv
import copy
import time
from datetime import datetime, timedelta
from io import StringIO
from odoo import models, fields, api, _, Command
from odoo.exceptions import UserError
from ..reportTypes import ReportType

STOCK_MOVE = 'stock.move'
IR_MODEL = 'ir.model'
AMAZON_STOCK_ADJUSTMENT_REPORT_HISTORY = 'amazon.stock.adjustment.report.history'
AMAZON_ADJUSTMENT_REASON_CODE = 'amazon.adjustment.reason.code'
COMMON_LOG_BOOK_EPT = 'common.log.book.ept'
AMAZON_SELLER_EPT = 'amazon.seller.ept'
IR_ATTACHMENT = 'ir.attachment'
DATE_YMDHMS = '%Y-%m-%d %H:%M:%S'
DATE_YMDTHMS = "%Y-%m-%dT%H:%M:%S"


class StockAdjustmentReportHistory(models.Model):
    """
    Added class to create an stock adjustment report record to import the stock report and process
    to create an stock move based on that.
    """
    _name = "amazon.stock.adjustment.report.history"
    _description = "Stock Adjustment Report"
    _inherit = ['mail.thread', 'amazon.reports']
    _order = 'id desc'

    @api.depends('seller_id')
    def _compute_get_company(self):
        """
        This method will set the company.
        """
        for record in self:
            company_id = record.seller_id.company_id.id if record.seller_id else False
            if not company_id:
                company_id = self.env.company.id
            record.company_id = company_id

    def _compute_get_moves_count(self):
        """
        This will count the number of stock moves.
        """
        stock_move_obj = self.env[STOCK_MOVE]
        self.moves_count = stock_move_obj.search_count([('amz_stock_adjustment_report_id', '=', self.id)])

    def _compute_get_log_count(self):
        """
        Find all stock moves associated with this report
        :return:
        """
        log_obj = self.env[COMMON_LOG_BOOK_EPT]
        model_id = self.env[IR_MODEL]._get(AMAZON_STOCK_ADJUSTMENT_REPORT_HISTORY).id
        log_ids = log_obj.search([('res_id', '=', self.id), ('model_id', '=', model_id)]).ids
        self.log_count = log_ids.__len__()

        # Set the boolean field mismatch_details as True if found any mismatch details in log lines
        if self.env['common.log.lines.ept'].search_count(
                [('log_book_id', 'in', log_ids), ('mismatch_details', '=', True)]):
            self.mismatch_details = True
        else:
            self.mismatch_details = False

    name = fields.Char(size=256)
    state = fields.Selection(
        [('draft', 'Draft'), ('_SUBMITTED_', 'SUBMITTED'), ('_IN_PROGRESS_', 'IN_PROGRESS'),
         ('_CANCELLED_', 'CANCELLED'), ('_DONE_', 'Report Received'),
         ('_DONE_NO_DATA_', 'DONE_NO_DATA'), ('processed', 'PROCESSED'),
         ('partially_processed', 'Partially Processed')], string='Report Status', default='draft')
    seller_id = fields.Many2one(AMAZON_SELLER_EPT, string='Seller', copy=False,
                                help="Select Seller id from you wanted to get Shipping report")
    attachment_id = fields.Many2one(IR_ATTACHMENT, string="Attachment")
    instance_id = fields.Many2one("amazon.instance.ept", string="Marketplace")
    report_id = fields.Char(string='Report ID', readonly='1')
    report_type = fields.Char(size=256, help="Amazon Report Type")
    report_request_id = fields.Char(string='Report Request ID', readonly='1')
    start_date = fields.Datetime(help="Report Start Date")
    end_date = fields.Datetime(help="Report End Date")
    requested_date = fields.Datetime(default=time.strftime(DATE_YMDHMS), help="Report Requested Date")
    company_id = fields.Many2one('res.company', string="Company", copy=False, compute="_compute_get_company",
                                 store=True)
    amz_stock_adjustment_report_ids = fields.One2many(STOCK_MOVE, 'amz_stock_adjustment_report_id',
                                                      string="Stock adjustment move ids")
    moves_count = fields.Integer(compute="_compute_get_moves_count", string="Move Count", store=False)
    log_count = fields.Integer(compute="_compute_get_log_count", store=False)
    mismatch_details = fields.Boolean(compute="_compute_get_log_count", string="Mismatch Details",
                                      help="True if any mismatch detail found in log line")

    @api.onchange('seller_id')
    def on_change_seller_id(self):
        """
        This Method relocates check seller and write start date and end date.
        :return: This Method return updated value.
        """
        if self.seller_id:
            self.start_date = datetime.now() - timedelta(self.seller_id.inv_adjustment_report_days)
            self.end_date = datetime.now()

    def unlink(self):
        """
        This Method if report is processed then raise UserError.
        """
        for report in self:
            if report.state == 'processed' or report.state == 'partially_processed':
                raise UserError(_('You cannot delete processed report.'))
        return super(StockAdjustmentReportHistory, self).unlink()

    @api.model
    def default_get(self, field):
        """
        This method will be useful for set default fields while creating stock adjustment report.
        :param field: fields
        :return: response dict
        """
        res = super(StockAdjustmentReportHistory, self).default_get(field)
        if field:
            res.update({'report_type': ReportType.GET_FBA_FULFILLMENT_INVENTORY_ADJUSTMENTS_DATA})
        return res

    @api.model
    def create(self, vals):
        """
        Create sequence for name of the reports
        :param vals:
        :return:
        """
        sequence = self.env.ref('amazon_ept.seq_inv_adjustment_report_job', raise_if_not_found=False)
        report_name = sequence.next_by_id() if sequence else '/'
        vals.update({'name': report_name})
        return super(StockAdjustmentReportHistory, self).create(vals)

    def list_of_process_logs(self):
        """
        List All Mismatch Details for Stock Adjustment Report.
        @author: Keyur Kanani
        :return:
        """
        model_id = self.env[IR_MODEL]._get(AMAZON_STOCK_ADJUSTMENT_REPORT_HISTORY).id
        action = {
            'domain': "[('res_id', '=', " + str(self.id) + "), ('model_id','='," + str(model_id) + ")]",
            'name': 'Stock Adjustment Report Logs',
            'view_mode': 'tree,form',
            'res_model': COMMON_LOG_BOOK_EPT,
            'type': 'ir.actions.act_window',
        }
        return action

    @api.model
    def auto_import_stock_adjustment_report(self, args={}):
        """
        Auto Import Stock Adjustment Reports
        :param args:
        :return:
        """
        seller_id = args.get('seller_id', False)
        if seller_id:
            seller = self.env[AMAZON_SELLER_EPT].browse(seller_id)
            if seller.stock_adjustment_report_last_sync_on:
                start_date = seller.stock_adjustment_report_last_sync_on
                start_date = datetime.strptime(str(start_date), DATE_YMDHMS)
                start_date = start_date - timedelta(hours=10)
            else:
                start_date = datetime.now() - timedelta(days=30)
            start_date = start_date + timedelta(days=seller.inv_adjustment_report_days * -1 or -3)
            start_date = start_date.strftime(DATE_YMDHMS)
            date_end = datetime.now()
            date_end = date_end.strftime(DATE_YMDHMS)
            inv_report = self.create({'seller_id': seller_id, 'start_date': start_date, 'end_date': date_end,
                                      'state': 'draft', 'requested_date': time.strftime(DATE_YMDHMS)})
            inv_report.with_context(is_auto_process=True).request_report()
            seller.write({'stock_adjustment_report_last_sync_on': date_end})
        return True

    @api.model
    def auto_process_stock_adjustment_report(self, args={}):
        """
        Process Stock adjustment reports
        :param args: {}
        :return: True
        """
        seller_id = args.get('seller_id', False)
        if seller_id:
            seller = self.env[AMAZON_SELLER_EPT].browse(seller_id)
            inv_reports = self.search([('seller_id', '=', seller.id),
                                       ('state', 'in', ['_SUBMITTED_', '_IN_PROGRESS_'])])
            for report in inv_reports:
                report.with_context(is_auto_process=True).get_report_request_list()
            inv_reports = self.search([('seller_id', '=', seller.id),
                                       ('state', 'in', ['_DONE_', '_SUBMITTED_', '_IN_PROGRESS_']),
                                       ('report_id', '!=', False)])
            for report in inv_reports:
                if report.report_id and report.state == '_DONE_' and not report.attachment_id:
                    report.with_context(is_auto_process=True).get_report()
                report.with_context(is_auto_process=True).process_stock_adjustment_report()
                self._cr.commit()
        return True

    def list_of_stock_moves(self):
        """
        Open tree view for list stock views
        :return:
        """
        stock_move_obj = self.env[STOCK_MOVE]
        records = stock_move_obj.search([('amz_stock_adjustment_report_id', '=', self.id)])
        action = {
            'domain': "[('id', 'in', " + str(records.ids) + " )]",
            'name': 'Amazon FBA Adjustment Stock Move',
            'view_mode': 'tree,form',
            'res_model': STOCK_MOVE,
            'type': 'ir.actions.act_window',
        }
        return action

    def create_amazon_report_attachment(self, result):
        """
        Get the Stock Adjustment Report as an attachment fot the report
        :param result: data
        :return:
        """
        seller = self.seller_id
        result = result.encode()
        result = base64.b64encode(result)
        file_name = "Stock_adjusments_report_" + time.strftime("%Y_%m_%d_%H%M%S") + '.csv'
        attachment = self.env[IR_ATTACHMENT].create({
            'name': file_name,
            'datas': result,
            'res_model': 'mail.compose.message',
            'type': 'binary'
        })
        self.message_post(body=_("<b>Stock adjustment Report Downloaded</b>"), attachment_ids=attachment.ids)
        self.write({'attachment_id': attachment.id})
        seller.write({'stock_adjustment_report_last_sync_on': datetime.now()})


    def process_stock_adjustment_report(self):
        """
        This Method process stock adjustment report.
        :return:This Method return boolean(True/False).
        """
        self.ensure_one()
        ir_cron_obj = self.env['ir.cron']
        if not self._context.get('is_auto_process', False):
            ir_cron_obj.with_context({'raise_warning': True}).find_running_schedulers(
                'ir_cron_process_fba_stock_adjustment_report_seller_', self.seller_id.id)
        common_log_book_obj = self.env[COMMON_LOG_BOOK_EPT]
        if not self.attachment_id:
            raise UserError(_("There is no any report are attached with this record."))
        if not self.seller_id:
            raise UserError(_("Seller is not defind for processing report"))
        model_id = self.env[IR_MODEL]._get(AMAZON_STOCK_ADJUSTMENT_REPORT_HISTORY).id
        job = common_log_book_obj.amazon_search_or_create_transaction_log('import', model_id, self.id)
        create_log = bool(self.state != 'partially_processed')
        group_wise_lines_list, partially_processed = self._prepare_group_wise_lines_list_ept(job)
        if group_wise_lines_list:
            partially_processed = self._process_group_wise_lines(group_wise_lines_list, job,
                                                                 partially_processed, create_log)
            if partially_processed:
                self.write({'state': 'partially_processed'})
            else:
                self.write({'state': 'processed'})
        if not job.log_lines:
            job.unlink()
        return True

    @staticmethod
    def get_amazon_group_wise_lines_list(row, config, group_wise_lines_list):
        """
        Prepare stock adjustment list as per it's Groups configurations.
        """
        if config.id in group_wise_lines_list:
            group_wise_lines_list.get(config.id).append(row)
        else:
            group_wise_lines_list.update({config.id: [row]})
        return group_wise_lines_list

    def _prepare_group_wise_lines_list_ept(self, job):
        """
        Prepare stock adjustment list as per it's Groups configurations.
        :param job: log record.
        :return: prepared stock adjustment list and process status
        """
        amazon_adjustment_reason_code_obj = self.env[AMAZON_ADJUSTMENT_REASON_CODE]
        amazon_stock_adjustment_config_obj = self.env['amazon.stock.adjustment.config']
        partially_processed = False
        group_wise_lines_list = {}
        imp_file = StringIO(base64.b64decode(self.attachment_id.datas).decode())
        reader = csv.DictReader(imp_file, delimiter='\t')
        reason_codes = amazon_adjustment_reason_code_obj.search([('group_id', '!=', False)])
        stock_config = amazon_stock_adjustment_config_obj.search([('seller_id', '=', self.seller_id.id)])
        for row in reader:
            reason = row.get('reason', '')
            if not reason:
                continue
            code = reason_codes.filtered(lambda l, reason=reason: l.name == reason)
            if not code:
                partially_processed = True
                job.write({'log_lines': [Command.create({'message': 'Code %s configuration not found for processing' %
                                                                   (reason), 'mismatch_details': True})]})
                continue
            if len(code.ids) > 1:
                partially_processed = True
                job.write({'log_lines': [Command.create({'message': 'Multiple Code %s configuration found for processing' % (reason),
                                                         'mismatch_details': True})]})
                continue
            config = stock_config.filtered(lambda l, code=code: l.group_id.id == code.group_id.id)
            if not config:
                partially_processed = True
                job.write({'log_lines': [Command.create({'message': 'Seller wise code %s configuration not found for processing' % (code.name),
                                                         'mismatch_details': True})]})
                continue
            if not config.is_send_email and not config.location_id and not config.group_id.id == self.env.ref('amazon_ept.amazon_damaged_inventory_ept').id:
                partially_processed = True
                if not config.location_id:
                    message = 'Location not configured for stock adjustment config ERP Id %s || group name %s' % (
                        config.id, config.group_id.name)
                    job.write({'log_lines': [Command.create({'message': message, 'mismatch_details': True})]})
                continue
            group_wise_lines_list = self.get_amazon_group_wise_lines_list(row, config, group_wise_lines_list)
        return group_wise_lines_list, partially_processed

    def _process_group_wise_lines(self, group_of_data, job, partially_processed, create_log):
        """
        This Method represent process prepare group wise line
        :param group_of_data: This arguments represent group data of amazon.
        :param job: This arguments represent log job of amazon.
        :param model_id: This arguments represent model id.
        :param partially_processed: This arguments represent state of process (True/False).
        :param create_log: This arguments represent create log (True/False).
        :return: This Method returns the state of adjustment report process.
        """
        amazon_stock_adjustment_config_obj = self.env['amazon.stock.adjustment.config']
        for config, lines in group_of_data.items():
            lines.reverse()
            config = amazon_stock_adjustment_config_obj.browse(config)
            if config.is_send_email:
                # create_email_of_unprocess_lines This Method represents the unprocessed line
                # that creates attachment and sent the attachment to the client.
                self.create_email_of_unprocess_lines(config, lines)
                continue
            if config.group_id.is_counter_part_group:
                partially_processed = self.process_counter_part_lines(config, lines, job, partially_processed,
                                                                      create_log)
            else:
                partially_processed = self.process_non_counter_part_lines(config, lines, job, partially_processed)
        return partially_processed

    def create_email_of_unprocess_lines(self, config, lines):
        """
        This Method represents the unprocessed line that creates attachment and sent the attachment to the client.
        :param config: These arguments represent config of group lines.
        :param lines: This arguments represent lines of group data items.
        :param job: This arguments represent log job of amazon.
        :return: This Method returns boolean(True/False).
        """
        template = config.email_template_id
        subtype_xmlid = 'amazon_ept.amazon_stock_adjustment_subtype_ept' if template else False
        field_names = []
        buff = StringIO()
        for line in lines:
            if not field_names:
                field_names = line.keys()
                csvwriter = csv.DictWriter(buff, field_names, delimiter='\t')
                csvwriter.writer.writerow(field_names)
            csvwriter.writerow(line)
        buff.seek(0)
        file_data = buff.read()
        vals = {
            'name': 'inv_unprocessed_lines.csv',
            'datas': base64.b64encode(file_data.encode()),
            'type': 'binary',
            'res_model': AMAZON_STOCK_ADJUSTMENT_REPORT_HISTORY,
        }
        attachment = self.env[IR_ATTACHMENT].create(vals)
        if template:
            body = template._render_field(
                'body_html', self.ids, compute_lang=True, post_process=True)[self.id]
            subject = template._render_field(
                'subject', self.ids, compute_lang=True)[self.id]
            message_type = 'email'
            self.message_post(subject=subject, message_type=message_type, body=body,
                              subtype_xmlid=subtype_xmlid, attachment_ids=attachment.ids)
        return True

    def process_counter_part_lines(self, config, lines, job, partially_processed, create_log):
        """
        This Method represents the processed counter part lines.
        :param config: These arguments represent config of group lines.
        :param lines: This arguments represent lines of group data items.
        :param job: This arguments represent log job of amazon.
        :param model_id: This arguments represent model id.
        :param partially_processed: This arguments represent state of process (True/False).
        :param create_log: This arguments represent create log (True/False).
        :return: This Method returns the state of adjustment report process.
        """
        temp_lines = copy.copy(lines)
        transaction_item_ids = []
        amazon_adjustment_reason_code_obj = self.env[AMAZON_ADJUSTMENT_REASON_CODE]
        counter_line_list = []
        code_dict = {}
        reason_codes = amazon_adjustment_reason_code_obj.search([('group_id', '=', config.group_id.id)])
        for line in lines:
            if line.get('transaction-item-id', False) in transaction_item_ids:
                continue
            reason = line.get('reason', '')
            if line.get('reason', '') not in code_dict:
                reason_code = reason_codes.filtered(lambda l, line=line: l.name == line.get('reason', ''))
                code_dict.update({line.get('reason', ''): reason_code})
            code = code_dict.get(line.get('reason', ''))
            if not code:
                continue
            counter_part_code = code.counter_part_id.name
            if not counter_part_code:
                continue
            args = {'line': line, 'temp_lines': temp_lines,
                    'counter_part_code': code.counter_part_id.name,
                    'reason': reason, 'create_log': create_log, 'job': job}
            counter_line_list = self._prepare_counter_line_list(transaction_item_ids, counter_line_list, args)
        if counter_line_list:
            stock_move_ids = self._amz_process_counter_line_list_ept(counter_line_list, code_dict, reason_codes, job)
            if stock_move_ids:
                self._prepare_stock_move_create(stock_move_ids)
        return partially_processed

    def _amz_process_counter_line_list_ept(self, counter_line_list, code_dict, reason_codes, job):
        """
        Process counter part lines list, Find and create stock move if not exist.
        :param counter_line_list:
        :param code_dict:
        :param reason_codes:
        :param job:
        :return: stock_move_ids []
        """
        stock_move_obj = self.env[STOCK_MOVE]
        fulfillment_center_obj = self.env['amazon.fulfillment.center']
        fulfillment_warehouse = {}
        stock_move_ids = []
        for counter_line in counter_line_list:
            line = counter_line[0]
            p_line = counter_line[1]
            product = self._find_amazon_product_for_process_adjustment_line(line, job)
            if not product:
                continue
            adjustment_date = self._amz_get_adjustment_date(p_line.get('adjusted-date', False))
            counter_vals = {
                'p_line_qty': float(p_line.get('quantity', 0.0)),
                'transaction_item_id': p_line.get('transaction-item-id', False),
                'fulfillment_center_id': p_line.get('fulfillment-center-id', False),
                'p_line_disposition': p_line.get('disposition', False),
                'other_line_disposition': line.get('disposition', False),
                'adjustment_date': adjustment_date
            }
            if counter_vals.get('fulfillment_center_id', '') not in fulfillment_warehouse:
                fulfillment_center = fulfillment_center_obj.search(
                    [('center_code', '=', counter_vals.get('fulfillment_center_id', '')),
                     ('seller_id', '=', self.seller_id.id)], limit=1)
                fn_warehouse = fulfillment_center.warehouse_id if fulfillment_center else False
                if not fn_warehouse or ((counter_vals.get('p_line_disposition', '') != 'SELLABLE' or
                                         counter_vals.get('other_line_disposition', '') != 'SELLABLE')
                                        and not fn_warehouse.unsellable_location_id):
                    if not fn_warehouse:
                        message = 'Warehouse not found for fulfillment center %s || Product %s' % (
                            counter_vals.get('fulfillment_center_id', False), line.get('sku', ''))
                    else:
                        message = 'Unsellable location not found for Warehouse %s || Product %s' % (
                            fn_warehouse.name, line.get('sku', ''))
                    job.write({'log_lines': [Command.create({'message': 'Mismatch: ' + message,
                                                             'mismatch_details': True})]})
                    continue
                fulfillment_warehouse.update(
                    {counter_vals.get('fulfillment_center_id', False): [fn_warehouse, fulfillment_center]})
            warehouse = fulfillment_warehouse.get(counter_vals.get('fulfillment_center_id', False), [False])[0]
            fulfillment_center = fulfillment_warehouse.get(counter_vals.get('fulfillment_center_id', False), [False])[1]
            if p_line.get('reason', '') not in code_dict:
                reason_code = reason_codes.filtered(lambda l, p_line=p_line: l.name == p_line.get('reason', ''))
                code_dict.update({p_line.get('reason', ''): reason_code})
            code = code_dict.get(p_line.get('reason', ''))
            counter_vals.update({'code': code, 'fulfillment_center': fulfillment_center.id, 'warehouse': warehouse})
            exist_move_domain = self._amz_prepare_existing_stock_move_domain(product, counter_vals)
            exist_stock_move = stock_move_obj.search(exist_move_domain)
            if exist_stock_move:
                job.write({'log_lines': [Command.create({'message': 'Line already processed for Product %s || Code %s-%s'
                                                                    % (product.name or False, p_line.get('reason', ''),
                                                                       line.get('reason', ''))})]})
            else:
                vals = self._amz_adjust_prepare_stock_move_vals_ept(product, counter_vals)
                vals.update({'state': 'confirmed'})
                stock_move = stock_move_obj.create(vals)
                stock_move_ids.append(stock_move.id)
        return stock_move_ids

    @staticmethod
    def _amz_prepare_existing_stock_move_domain(product, counter_vals):
        """
        Prepare domain for search exiting stock moves
        :param product: product.product()
        :param counter_vals: {}
        :return: []
        """
        warehouse = counter_vals.get('warehouse')
        exist_move_domain = [('product_uom_qty', '=', counter_vals.get('p_line_qty', 0.0)),
                             ('product_id', '=', product.id),
                             ('adjusted_date', '=', counter_vals.get('adjustment_date', '')),
                             ('transaction_item_id', '=', counter_vals.get('transaction_item_id', False)),
                             ('fulfillment_center_id', '=', counter_vals.get('fulfillment_center', False)),
                             ('code_id', '=', counter_vals.get('code', False).id)]
        destination_location_id = warehouse.unsellable_location_id.id if counter_vals.get(
            'p_line_disposition', '') != 'SELLABLE' else warehouse.lot_stock_id.id
        source_location_id = warehouse.unsellable_location_id.id if counter_vals.get(
            'other_line_disposition', '') != 'SELLABLE' else warehouse.lot_stock_id.id
        exist_move_domain += [('location_id', '=', source_location_id),
                              ('location_dest_id', '=', destination_location_id)]
        counter_vals.update({'source_location_id': source_location_id,
                             'destination_location_id': destination_location_id})
        return exist_move_domain

    @staticmethod
    def get_amazon_source_and_destination_location_id(counter_vals, config, warehouse):
        """
        This method will return so find and return source and destination location
        """
        if counter_vals.get('p_line_qty', 0.0) < 0.0:
            destination_location_id = config.location_id.id
            source_location_id = warehouse.lot_stock_id.id if counter_vals.get(
                'disposition', '') == 'SELLABLE' else warehouse.unsellable_location_id.id
        else:
            source_location_id = config.location_id.id
            destination_location_id = warehouse.lot_stock_id.id if counter_vals.get(
                'disposition', '') == 'SELLABLE' else warehouse.unsellable_location_id.id
        return source_location_id, destination_location_id

    def process_non_counter_part_lines(self, config, lines, job, partially_processed):
        """
         This Method represents processed non-counterpart lines.
         : param config: These arguments represent the config of group lines.
         : param lines: These arguments represent lines of group data items.
         : param job: These arguments represent the log job of amazon.
         : param partially_processed: These arguments represent the state of the process (True/False).
         : return: This Method returns the state of adjustment report process.
         """
        amazon_adjustment_reason_code_obj = self.env[AMAZON_ADJUSTMENT_REASON_CODE]
        fulfillment_center_obj = self.env['amazon.fulfillment.center']
        stock_move_ids = []
        fulfillment_center_dict = {}
        stock_move_obj = self.env[STOCK_MOVE]
        reason_code = amazon_adjustment_reason_code_obj.search([('group_id', '=', config.group_id.id)])
        for line in lines:
            product = self._find_amazon_product_for_process_adjustment_line(line, job)
            if not product:
                continue
            fulfillment_center, warehouse, skip_line = self._amz_find_fulfillment_center_warehouse(
                line, fulfillment_center_dict, job, fulfillment_center_obj)
            if skip_line:
                partially_processed = True
                continue
            counter_vals = self.prepare_amz_non_counter_line_vals(line, reason_code, fulfillment_center, warehouse)
            exist_move_domain = self.prepare_existing_move_domain(product, counter_vals)
            source_location_id, destination_location_id = self.get_amazon_source_and_destination_location_id(
                counter_vals, config, warehouse)
            exist_move_domain += [('location_id', '=', source_location_id),
                                  ('location_dest_id', '=', destination_location_id)]
            exist_move = stock_move_obj.search(exist_move_domain)
            counter_vals.update({'source_location_id': source_location_id,
                                 'destination_location_id': destination_location_id})
            if exist_move:
                job.write({'log_lines': [Command.create({'message': 'Line already processed for Product %s || Code %s'
                                                                   % (product.name, line.get('reason', ''))})]})
            else:
                vals = self._amz_adjust_prepare_stock_move_vals_ept(product, counter_vals)
                stock_move = stock_move_obj.create(vals)
                stock_move_ids.append(stock_move.id)
        # This Method prepare value for stock move,stock move line and create stock move,stock moveline
        if stock_move_ids:
            self._prepare_stock_move_create(stock_move_ids)
        return partially_processed

    def prepare_existing_move_domain(self, product, counter_vals):
        """
        Prepare domain for search existing stock move.
        :param product: product.product()
        :param counter_vals: dict{}
        :return: list of domain [(),()]
        """
        exist_move_domain = [('product_uom_qty', '=', abs(counter_vals.get('p_line_qty', 0.0))),
                             ('product_id', '=', product.id),
                             ('adjusted_date', '=', counter_vals.get('adjustment_date', '')),
                             ('transaction_item_id', '=', counter_vals.get('transaction_item_id', False)),
                             ('fulfillment_center_id', '=', counter_vals.get('fulfillment_center', False)),
                             ('code_id', '=', counter_vals.get('code', False).id)]
        return exist_move_domain

    def prepare_amz_non_counter_line_vals(self, line, reason_code, fulfillment_center, warehouse):
        """
        Prepare Values of counter lines
        :param line: dict {}
        :return: dict {}
        """
        adjustment_date = self._amz_get_adjustment_date(line.get('adjusted-date', False))
        reason = line.get('reason', '')
        code = reason_code.filtered(lambda l, reason=reason: l.name == reason)
        counter_vals = {
            'p_line_qty': float(line.get('quantity', 0.0)),
            'disposition': line.get('disposition', ''),
            'transaction_item_id': line.get('transaction-item-id', False),
            'adjustment_date': adjustment_date,
            'code': code,
            'fulfillment_center': fulfillment_center.id,
            'warehouse': warehouse
        }
        return counter_vals

    def _amz_find_fulfillment_center_warehouse(self, line, fulfillment_center_dict, job, fcenter_obj):
        """
        Get fulfillment center and warehouse
        :param line: dict {}
        :param fulfillment_center_dict: dict {}
        :param job: common.log.book.ept()
        :param fcenter_obj: amazon.fulfillment.center()
        :return:
        """
        skip_line = False
        if line.get('fulfillment-center-id', False) not in fulfillment_center_dict:
            fulfillment_center = fcenter_obj.search([('center_code', '=', line.get('fulfillment-center-id', False)),
                                                     ('seller_id', '=', self.seller_id.id)], limit=1)
            fulfillment_center_dict.update({line.get('fulfillment-center-id', False): fulfillment_center or False})
        fulfillment_center = fulfillment_center_dict.get(line.get('fulfillment-center-id', False), False)
        warehouse = fulfillment_center.warehouse_id if fulfillment_center else False
        if not warehouse or (line.get('disposition', '') == 'UNSELLABLE' and not warehouse.unsellable_location_id):
            if not warehouse:
                message = 'Warehouse not found for fulfillment center %s || Product %s' % (
                    line.get('fulfillment-center-id', False), line.get('sku', ''))
            else:
                message = 'Unsellable location not found for Warehouse %s' % (warehouse.name)
            job.write({'log_lines': [Command.create({'message': 'Mismatch: ' + message,
                                                     'mismatch_details': True})]})
            skip_line = True
        return fulfillment_center, warehouse, skip_line

    @staticmethod
    def _amz_get_adjustment_date(date):
        """
        Format adjustment date in proper timezone
        :param date: datetime()
        :return: datetime()
        """
        try:
            adjustment_date = time.mktime(datetime.strptime(date, "%Y-%m-%dT%H:%M:%S%z").timetuple())
            adjustment_date = datetime.fromtimestamp(adjustment_date)
        except Exception:
            adjustment_date = date[:len(date)-3] + date[len(date)-2:]
            adjustment_date = time.mktime(datetime.strptime(adjustment_date, "%Y-%m-%dT%H:%M:%S%z").timetuple())
            adjustment_date = datetime.fromtimestamp(adjustment_date)
        return adjustment_date

    def _amz_adjust_prepare_stock_move_vals_ept(self, product, counter_vals):
        """
        Prepare values for create adjustment stock move
        :param product: product.product()
        :param counter_vals: {}
        :return: {}
        """
        return {
            'product_uom_qty': abs(counter_vals.get('p_line_qty', 0.0)),
            'product_id': product.id,
            'product_uom': product.uom_id.id,
            'state': 'draft',
            'adjusted_date': counter_vals.get('adjustment_date', ''),
            'origin': self.name,
            'name': product.name,
            'transaction_item_id': counter_vals.get('transaction_item_id', ''),
            'fulfillment_center_id': counter_vals.get('fulfillment_center', False),
            'code_id': counter_vals.get('code').id,
            'location_id': counter_vals.get('source_location_id', False),
            'location_dest_id': counter_vals.get('destination_location_id', False),
            'code_description': counter_vals.get('code').description,
            'amz_stock_adjustment_report_id': self.id
        }

    def _prepare_stock_move_create(self, stock_move_ids):
        """
        This Method represents to prepare stock move value and stock move create.
        :param stock_move_ids: This arguments represents stock move ids list.
        :return: This Method returns boolean(True/False).
        """
        stock_move_obj = self.env[STOCK_MOVE]
        for stock_move_id in stock_move_ids:
            stock_move = stock_move_obj.browse(stock_move_id)
            stock_move._action_confirm()
            stock_move._action_assign()
            stock_move._set_quantity_done(stock_move.product_uom_qty)
            stock_move._action_done()
        return True

    @staticmethod
    def _prepare_counter_line_list(transaction_item_ids, counter_line_list, args):
        """
        This Method represents to prepare a list of counterpart lines.
        :param transaction_item_ids: []
        :param counter_line_list: []
        :param args: {}
        :return: []
        """
        line = args.get('line', False)
        job = args.get('job', False)
        for temp_line in args.get('temp_lines', False):
            if temp_line.get('reason', '') == args.get('counter_part_code', '') and \
                    abs(float(temp_line.get('quantity', 0.0))) == abs(float(line.get('quantity', 0.0))) and \
                    temp_line.get('transaction-item-id', False) not in transaction_item_ids:
                if line.get('adjusted-date', '') == temp_line.get('adjusted-date', '') and \
                        line.get('fnsku', '') == temp_line.get('fnsku', '') and \
                        line.get('sku', '') == temp_line.get('sku', '') and \
                        line.get('fulfillment-center-id', False) == temp_line.get('fulfillment-center-id', False):
                    transaction_item_ids.append(temp_line.get('transaction-item-id', False))
                    counter_line_list.append((line, temp_line))
                    message = """Counter Part Combination line || sku : {} || adjustment-date {} || 
                    fulfillment-center-id {} || quantity {} || Code {} - Disposition {}
                    & {} - Disposition {}""".format(line.get('sku', ''), line.get('adjusted-date', ''),
                                                    line.get('fulfillment-center-id', False),
                                                    line.get('quantity', 0.0), args.get('reason', ''),
                                                    line.get('disposition', ''), temp_line.get('reason', ''),
                                                    temp_line.get('disposition', ''))
                    if args.get('create_log', False):
                        job.write({'log_lines': [Command.create({'message': message})]})
                    break
        return counter_line_list

    def _find_amazon_product_for_process_adjustment_line(self, line, job):
        """
        This Method represents search amazon product for product adjustment line.
        :param line: These arguments represent the line of amazon.
        :param job: These arguments represent the log job of amazon.
        :return: This Method return product.
        """
        amazon_product_obj = self.env['amazon.product.ept']
        sku = line.get('sku', '')
        asin = line.get('fnsku', '')
        amazon_product = amazon_product_obj.search([('seller_sku', '=', sku), ('fulfillment_by', '=', 'FBA')], limit=1)
        if not amazon_product:
            amazon_product = amazon_product_obj.search([('product_asin', '=', asin), ('fulfillment_by', '=', 'FBA')],
                                                       limit=1)
        product = amazon_product.product_id if amazon_product else False
        if not amazon_product and job:
            job.write({'log_lines': [Command.create({'message': 'Product  not found for SKU %s & ASIN %s'
                                                                % (sku, asin), 'mismatch_details': True})]})
        return product
