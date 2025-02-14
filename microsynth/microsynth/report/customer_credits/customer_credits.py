# Copyright (c) 2022-2023, libracore AG and contributors
# For license information, please see license.txt

from __future__ import unicode_literals
import frappe
from frappe import _

def execute(filters=None):
    columns = get_columns()
    data = get_data(filters)
    return columns, data

def get_columns():
    columns = [
        {"label": _("Date"), "fieldname": "date", "fieldtype": "Date", "width": 80},
        {"label": _("Customer"), "fieldname": "customer", "fieldtype": "Link", "options": "Customer", "width": 80},
        {"label": _("Customer name"), "fieldname": "customer_name", "fieldtype": "Data", "width": 200},
        {"label": _("Sales Invoice"), "fieldname": "sales_invoice", "fieldtype": "Link", "options": "Sales Invoice", "width": 150},
        {"label": _("Currency"), "fieldname": "currency", "fieldtype": "Link", "options": "Currency", "width": 80},
        {"label": _("Net Amount"), "fieldname": "net_amount", "fieldtype": "Currency", "width": 150, 'options': 'currency'},
        {"label": _("Outstanding"), "fieldname": "outstanding", "fieldtype": "Currency", "width": 150, 'options': 'currency'},
        {"label": _("Status"), "fieldname": "status", "fieldtype": "Data", "width": 100},
        {"label": _("Reference"), "fieldname": "reference", "fieldtype": "Link", "options": "Sales Invoice", "width": 80}
    ]
    return columns

def get_data(filters, short=False):
    conditions = ""
    if filters.get('customer'):
    
        # customer based evaluation: ledger
        sql_query = """
        SELECT
            `raw`.`type` AS `type`,
            `raw`.`date` AS `date`,
            `raw`.`customer` AS `customer`,
            `raw`.`customer_name` AS `customer_name`,
            `raw`.`sales_invoice` AS `sales_invoice`,
            `raw`.`net_amount` AS `net_amount`,
            `raw`.`status` AS `status`,
            `raw`.`reference` AS `reference`,
            `raw`.`currency` AS `currency`
        FROM (
            SELECT
                "Credit" AS `type`,
                `tabSales Invoice`.`posting_date` AS `date`,
                `tabSales Invoice`.`customer` AS `customer`,
                `tabSales Invoice`.`customer_name` AS `customer_name`,
                `tabSales Invoice`.`name` AS `sales_invoice`,
                `tabSales Invoice Item`.`net_amount` AS `net_amount`,
                `tabSales Invoice`.`status` AS `status`,
                `tabSales Invoice Item`.`name` AS `reference`,
                `tabSales Invoice`.`currency` AS `currency`
            FROM `tabSales Invoice Item` 
            LEFT JOIN `tabSales Invoice` ON `tabSales Invoice Item`.`parent` = `tabSales Invoice`.`name`
            WHERE 
                `tabSales Invoice`.`docstatus` = 1
                AND `tabSales Invoice Item`.`item_code` = "{credit_item}"
                AND `tabSales Invoice`.`customer` = "{customer}"
                AND `tabSales Invoice`.`company` = "{company}"
                
            UNION SELECT
                "Allocation" AS `type`,
                `tabSales Invoice`.`posting_date` AS `date`,
                `tabSales Invoice`.`customer` AS `customer`,
                `tabSales Invoice`.`customer_name` AS `customer_name`,
                `tabSales Invoice`.`name` AS `sales_invoice`,
                ( IF (`tabSales Invoice`.`is_return` = 1, 1, -1) * `tabSales Invoice Customer Credit`.`allocated_amount`) AS `net_amount`,
                `tabSales Invoice`.`status` AS `status`,
                `tabSales Invoice Customer Credit`.`sales_invoice` AS `reference`,
                `tabSales Invoice`.`currency` AS `currency`
            FROM `tabSales Invoice Customer Credit` 
            LEFT JOIN `tabSales Invoice` ON `tabSales Invoice Customer Credit`.`parent` = `tabSales Invoice`.`name`
            WHERE 
                `tabSales Invoice`.`docstatus` = 1
                AND `tabSales Invoice`.`customer` = "{customer}"
                AND `tabSales Invoice`.`company` = "{company}"
        ) AS `raw`
        ORDER BY `raw`.`date` DESC, `raw`.`sales_invoice` DESC;
        """.format(credit_item=frappe.get_value("Microsynth Settings", "Microsynth Settings", "credit_item"), 
            customer=filters.get('customer'),
            company=filters.get('company'))
    
        data = frappe.db.sql(sql_query, as_dict=True)
        
        credit_positions = {}
        # find open balances
        for d in data:
            if d['type'] == "Credit":
                # open credit
                if not d['sales_invoice'] in credit_positions:
                    credit_positions[d['sales_invoice']] = 0
                credit_positions[d['sales_invoice']] += d['net_amount']
            else:
                # deduct allocation:
                if not d['reference'] in credit_positions:
                    credit_positions[d['reference']] = 0
                credit_positions[d['reference']] += d['net_amount']
        
        # apply to credits
        for d in data:
            if d['type'] == "Credit":
                d['outstanding'] = credit_positions[d['sales_invoice']]
                
        # shorten output
        if short:
            output = []
            for d in data:
                if d['type'] == "Credit" and d['outstanding'] > 0:
                    output.append(d)
            data = output
    else:
        # overview, group by customer
        sql_query = """
        SELECT 
            `raw`.`customer` AS `customer`,
            `raw`.`customer_name` AS `customer_name`,
            SUM(`raw`.`net_amount`) AS `outstanding`,
            `raw`.`currency` AS `currency`
        FROM (
            SELECT
                "Credit" AS `type`,
                `tabSales Invoice`.`posting_date` AS `date`,
                `tabSales Invoice`.`customer` AS `customer`,
                `tabSales Invoice`.`customer_name` AS `customer_name`,
                `tabSales Invoice`.`name` AS `sales_invoice`,
                `tabSales Invoice Item`.`net_amount` AS `net_amount`,
                `tabSales Invoice`.`status` AS `status`,
                `tabSales Invoice Item`.`name` AS `reference`,
                `tabSales Invoice`.`currency` AS `currency`
            FROM `tabSales Invoice Item` 
            LEFT JOIN `tabSales Invoice` ON `tabSales Invoice Item`.`parent` = `tabSales Invoice`.`name`
            WHERE 
                `tabSales Invoice`.`docstatus` = 1
                AND `tabSales Invoice Item`.`item_code` = "{credit_item}"
                AND `tabSales Invoice`.`company` = "{company}"
                
            UNION SELECT
                "Allocation" AS `type`,
                `tabSales Invoice`.`posting_date` AS `date`,
                `tabSales Invoice`.`customer` AS `customer`,
                `tabSales Invoice`.`customer_name` AS `customer_name`,
                `tabSales Invoice`.`name` AS `sales_invoice`,
                ( IF (`tabSales Invoice`.`is_return` = 1, 1, -1) * `tabSales Invoice Customer Credit`.`allocated_amount`) AS `net_amount`,
                `tabSales Invoice`.`status` AS `status`,
                `tabSales Invoice Customer Credit`.`sales_invoice` AS `reference`,
                `tabSales Invoice`.`currency` AS `currency`
            FROM `tabSales Invoice Customer Credit` 
            LEFT JOIN `tabSales Invoice` ON `tabSales Invoice Customer Credit`.`parent` = `tabSales Invoice`.`name`
            WHERE 
                `tabSales Invoice`.`docstatus` = 1
                AND `tabSales Invoice`.`company` = "{company}"
        ) AS `raw`
        GROUP BY `raw`.`customer`
        ORDER BY `raw`.`customer` ASC;
        """.format(credit_item=frappe.get_value("Microsynth Settings", "Microsynth Settings", "credit_item"), 
            company=filters.get('company'))
    
    
        data = frappe.db.sql(sql_query, as_dict=True)
    
    return data
    
