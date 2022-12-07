# -*- coding: utf-8 -*-
# Copyright (c) 2022, libracore (https://www.libracore.com) and contributors
# For license information, please see license.txt
#
# For more details, refer to https://github.com/Microsynth/erp-microsynth/
#

import frappe
from frappe import _
from frappe.utils.background_jobs import enqueue
from microsynth.microsynth.report.invoiceable_services.invoiceable_services import get_data
from frappe.utils import cint
from erpnext.stock.doctype.delivery_note.delivery_note import make_sales_invoice
from erpnextswiss.erpnextswiss.attach_pdf import create_folder, execute
from frappe.utils.file_manager import save_file
from frappe.email.queue import send
from frappe.desk.form.load import get_attachments
import datetime
import json
import random

@frappe.whitelist()
def create_invoices(mode, company):
    kwargs={
        'mode': mode,
        'company': company
    }
    
    enqueue("microsynth.microsynth.invoicing.async_create_invoices",
        queue='long',
        timeout=15000,
        **kwargs)
    return {'result': _('Invoice creation started...')}
    
def async_create_invoices(mode, company):
    all_invoiceable = get_data(filters={'company': company})
    
    if (mode in ["Post", "Electronic"]):
        # individual invoices
        for dn in all_invoiceable:
            if cint(dn.get('collective_billing')) == 0:
                if mode == "Post":
                    if dn.get('invoicing_method') == "Post":
                        make_invoice(dn.get('delivery_note'))
                else:
                    if dn.get('invoicing_method') != "Post":
                        make_invoice(dn.get('delivery_note'))
    else:
        # colletive invoices
        customers = []
        for dn in all_invoiceable:
            if cint(dn.get('collective_billing')) == 1 and dn.get('customer') not in customers:
                customers.append(dn.get('customer'))
        
        # for each customer, create one invoice for all dns
        for c in customers:
            dns = []
            for dn in all_invoiceable:
                if cint(dn.get('collective_billing')) == 1 and dn.get('customer') == c:
                    dns.append(dn.get('delivery_note'))
                    
            if len(dns) > 0:
                make_collective_invoice(dns)
            
    return

def make_invoice(delivery_note):
    sales_invoice_content = make_sales_invoice(delivery_note)
    # compile document
    sales_invoice = frappe.get_doc(sales_invoice_content)
    sales_invoice.set_advances()    # get advances (customer credit)
    sales_invoice.insert()
    sales_invoice.submit()
    transmit_sales_invoice(sales_invoice.name)
    frappe.db.commit()
    return
    
def make_collective_invoice(delivery_notes):
    # create invoice from first delivery note
    sales_invoice_content = make_sales_invoice(delivery_notes[0])
    if len(delivery_notes) > 1:
        for i in range(1, len(delivery_notes)):
            # append items from other delivery notes
            sales_invoice_content = make_sales_invoice(source_name=delivery_notes[i], target_doc=sales_invoice_content)
    
    # compile document
    sales_invoice = frappe.get_doc(sales_invoice_content)
    sales_invoice.set_advances()    # get advances (customer credit)
    sales_invoice.insert()
    sales_invoice.submit()
    transmit_sales_invoice(sales_invoice.name)
    frappe.db.commit()
    return


def get_sales_order_list_and_delivery_note_list(sales_invoice): 
    """creates a dict with two keys sales_orders/delivery_notes with value of a list of respective ids"""

    sales_order_list = []
    delivery_note_list = []

    for item in sales_invoice.items:
        if item.sales_order and item.sales_order not in sales_order_list: 
            sales_order_list.append(item.sales_order)
        if item.delivery_note and item.delivery_note not in delivery_note_list: 
            delivery_note_list.append(item.sales_order)

    return {"sales_orders": sales_order_list, "delivery_notes": delivery_note_list}


def get_sales_order_id_and_delivery_note_id(sales_invoice): 
    """returns one sales_order_id and one or no delivery_note_id"""

    sos_and_dns = get_sales_order_list_and_delivery_note_list(sales_invoice)
    sales_orders = sos_and_dns["sales_orders"]
    delivery_notes = sos_and_dns["delivery_notes"]
    if len(sales_orders) < 1: 
        frappe.throw("no sales orders. case not known")
    elif len(sales_orders) > 1:
        frappe.throw("too many sales orders. case not implemented.")
    sales_order_id = sales_orders[0]

    delivery_note_id = ""
    if len(delivery_notes) < 1: 
        # may happen, accept this case!
        #frappe.throw("no delivery note")
        pass
    elif len(delivery_notes) > 1:
        frappe.throw("too many delivery notes. case not implemented.")
    else: 
        delivery_note_id = delivery_notes[0]

    return {"sales_order_id":sales_order_id, "delivery_note_id": delivery_note_id}


def create_list_of_item_dicts_for_cxml(sales_invoice):
    """creates a list of dictionaries of all items of a sales_invoice (including shipping item)"""

    list_of_invoiced_items = []
    invoice_item_dicts = {}
    invoice_position = 0
    invoice_other_items = {}

    # invoiced items and Shipping 
    for item in sales_invoice.items:
        invoice_item_dicts[item.item_code] = item
        if item.item_group == "Shipping": 
            # shipping
            invoiced_shipping = {}
            invoiced_shipping[item.name] = item
            invoiced_shipping["price"] = item.net_amount
            invoiced_shipping["shipping_name"] = item.item_name
            invoiced_shipping["position"] = len(sales_invoice.oligos) + 1
            list_of_invoiced_items.append(invoiced_shipping)
        elif item.item_group != "3.1 DNA/RNA Synthese": 
            # other items (labels)
            invoice_position += 1
            invoice_other_items["invoice_position"] = invoice_position
            invoice_other_items["quantity"] = item.qty
            invoice_other_items["price"] = item.price_list_rate
            list_of_invoiced_items.append(invoice_other_items)

    # oligos
    invoiced_oligos = {}
    for oligo_link in sales_invoice.oligos: 
        invoice_position += 1 
        oligo_object = frappe.get_doc("Oligo", oligo_link.as_dict()["oligo"])
        #print ("\nOLIGO '%s', OLIGO-Info:\n====\n%s"  %(oligo_link.as_dict()["oligo"], oligo_object.as_dict() ))
        oligo_details = {}
        oligo_details[oligo_object.name] = oligo_object
        oligo_details["invoice_position"] = invoice_position
        oligo_details["quantity"] = 1
        oligo_details["price"] = 0
        oligo_details["price"]
        oligo_details["customer_name"] = oligo_object.oligo_name
        for oligo_item in oligo_object.items:
            oligo_details["price"] += oligo_item.qty * invoice_item_dicts[oligo_item.item_code].rate
        invoiced_oligos[oligo_object.name] = oligo_details
    list_of_invoiced_items.append(invoiced_oligos)
    
    # print(list_of_invoiced_items)
    return list_of_invoiced_items


def get_shipping_item(items):
    for i in reversed(items):
        if i.item_group == "Shipping":
            print(i)
            return i.item_code


def create_country_name_to_code_dict(): 
    
    country_codes = {}
    country_query = frappe.get_all("Country", fields=['name', 'code'])
    for dict in country_query:
        country_codes[dict['name']] = dict['code']
    return country_codes


def create_dict_of_invoice_info_for_cxml(sales_invoice=None): 
    """ Doc string """

    print ("\n1a")
    #for key, value in (sales_invoice.as_dict().items()): 
    #    print ("%s: %s" %(key, value))

    shipping_address = frappe.get_doc("Address", sales_invoice.shipping_address_name)
    #for key, value in (shipping_address.as_dict().items()): 
    #    print ("%s: %s" %(key, value))

    print ("\n1b")
    billing_address = frappe.get_doc("Address", sales_invoice.customer_address)
    #for key, value in (billing_address.as_dict().items()): 
    #    print ("%s: %s" %(key, value))

    customer = frappe.get_doc("Customer", sales_invoice.customer)
    #for key, value in (customer.as_dict().items()): 
    #   print ("%s: %s" %(key, value))
    # print(customer.as_dict())

    print ("\n-----0-----")
    company_details = frappe.get_doc("Company", sales_invoice.company)
    #print(company_details.as_dict())
    #for key, value in (company_details.as_dict().items()): 
    #   print ("%s: %s" %(key, value))
    
    #for key, value in (company_details.as_dict().items()): 
    #   print ("%s: %s" %(key, value))
    # print(company_details.default_bank_account.split("-")[1].strip().split(" ")[1].strip())

    print ("\n-----0A-----")
    company_address = frappe.get_doc("Address", sales_invoice.company_address)
    #print(company_address.as_dict())

    print ("\n-----0B-----")
    try: 
        settings = frappe.get_doc("Microsynth Settings", "Microsynth Settings")
    except: 
        frappe.throw("Cannot access 'Microsynth Settings'. Invoice cannot be created")
    #print("settings: %s" % settings.as_dict())

    default_account = frappe.get_doc("Account", company_details.default_bank_account)
    if sales_invoice.currency == default_account.account_currency:
        bank_account = default_account
    else: 
        preferred_accounts = frappe.get_all("Account", 
                    filters = {
                        "company" : sales_invoice.company, 
                        "account_type" : "Bank",
                        "account_currency": sales_invoice.currency, 
                        "disabled": 0, 
                        "preferred": 1
                        },
                        fields = ["name"]
                    )
        if len(preferred_accounts) == 1: 
            preferred_account = frappe.get_doc("Account", preferred_accounts[0]["name"])
        else: 
            frappe.throw("No or too many valid bank account")
        
        bank_account = preferred_account
            
    #for key, value in (bank_account.as_dict().items()): 
    #   print ("%s: %s" %(key, value))

    #print(sales_invoice.as_dict()["taxes"][0]["creation"].strftime("%Y-%m-%dT%H:%M:%S+01:00"),
    #for key, value in (company_details.as_dict().items()): 
    #    print ("%s: %s" %(key, value))

    soId_and_dnId = get_sales_order_id_and_delivery_note_id(sales_invoice)
    sales_order_id = soId_and_dnId["sales_order_id"] # can be called directly in dict "data" creation on-the-fly
    delivery_note_id = soId_and_dnId["delivery_note_id"] # can be called directly in dict "data" creation on-the-fly

    country_codes = create_country_name_to_code_dict()
    itemList = create_list_of_item_dicts_for_cxml(sales_invoice)
    data2 = {'basics' : {'sender_network_id' :  settings.ariba_id,
                        'receiver_network_id':  customer.invoice_network_id,
                        'shared_secret':        settings.ariba_secret,
                        'paynet_sender_pid':    settings.paynet_id, 
                        'payload':              sales_invoice.creation.strftime("%Y%m%d%H%M%S") + str(random.randint(0, 10000000)) + "@microsynth.ch"
,
                        'order_id':             sales_invoice.po_no, 
                        'currency':             sales_invoice.currency,
                        'invoice_id':           sales_invoice.name,
                        'invoice_date':         sales_invoice.as_dict()["creation"].strftime("%Y-%m-%dT%H:%M:%S+01:00"),
                        'invoice_date_paynet':  sales_invoice.as_dict()["creation"].strftime("%Y%m%d"),
                        'delivery_note_id':     sales_invoice.items[0].delivery_note, 
                        'delivery_note_date_paynet':  "" # delivery_note.as_dict()["creation"].strftime("%Y%m%d"),
                        },
            'remitTo' : {'name':            sales_invoice.company,
                        'street':           company_address.address_line1, 
                        'pin':              company_address.pincode,
                        'city':             company_address.city, 
                        'iso_country_code': country_codes[company_address.country].upper(), 
                        'supplier_tax_id':  company_details.tax_id + ' MWST' 
                        },
            'billTo' : {'address_id':       billing_address.name, 
                        'name':             sales_invoice.customer_name,
                        'street':           billing_address.address_line1,
                        'pin':              billing_address.pincode,
                        'city':             billing_address.city,
                        'iso_country_code': country_codes[billing_address.country].upper()
                        },
            'from' :    {'name':            company_details.company_name,
                        'street':           company_address.address_line1, 
                        'pin':              company_address.pincode,
                        'city':             company_address.city,
                        'iso_country_code': country_codes[company_address.country].upper()
                        }, 
            'soldTo' :  {'address_id':      billing_address.name, 
                        'name':             sales_invoice.customer_name,
                        'street':           billing_address.address_line1,
                        'pin':              billing_address.pincode,
                        'city':             billing_address.city,
                        'iso_country_code': country_codes[billing_address.country].upper()
                        }, 
            'shipFrom' : {'name':           company_details.name, 
                        'street':           company_address.address_line1,
                        'pin':              company_address.pincode,
                        'city':             company_address.city,
                        'iso_country_code': country_codes[company_address.country].upper()
                        },
            'shipTo' : {'address_id':       shipping_address.customer_address_id,
                        'name':             shipping_address.name,
                        'street':           shipping_address.address_line1,
                        'pin':              shipping_address.pincode,
                        'city':             shipping_address.city,
                        'iso_country_code': country_codes[shipping_address.country].upper()
                        }, 
            'receivingBank' : {'swift_id':  bank_account.bic,
                        'iban_id':          bank_account.iban,
                        'account_name':     bank_account.company,
                        'account_id':       bank_account.iban,
                        'account_type':     'Checking',  
                        'branch_name':      bank_account.bank_name + " " + bank_account.bank_branch_name
                        }, 
            'extrinsic' : {'buyerVatId':                customer.tax_id + ' MWST',
                        'supplierVatId':                company_details.tax_id + ' MWST',
                        'supplierCommercialIdentifier': company_details.tax_id + ' VAT' 
                        }, 
            'items' :   itemList, 
            'tax' :     {'amount' :         sales_invoice.total_taxes_and_charges,
                        'taxable_amount' :  sales_invoice.net_total,
                        'percent' :         sales_invoice.taxes[0].rate if len(sales_invoice.taxes)>0 else 0, 
                        'taxPointDate' :    sales_invoice.posting_date.strftime("%Y-%m-%dT%H:%M:%S+01:00"),
                        'description' :     sales_invoice.taxes[0].description if len(sales_invoice.taxes)>0 else 0
                        },
            # shipping is listed on item level, not header level.
            'shippingTax' : {'taxable_amount':  '0.00',
                        'amount':               '0.00',
                        'percent':              '0.0',
                        'taxPointDate':         sales_invoice.posting_date.strftime("%Y-%m-%dT%H:%M:%S+01:00"),
                        'description' :         '0.0' + '% shipping tax'
                        }, 
            'summary' : {'subtotal_amount' :        sales_invoice.base_total,
                        'shipping_amount' :         '0.00',
                        'gross_amount' :            sales_invoice.net_total,
                        'total_amount_without_tax': sales_invoice.net_total,
                        'net_amount' :              sales_invoice.net_total,
                        'due_amount' :              sales_invoice.rounded_total
                        }
            }
    return data2


def transmit_sales_invoice():
#def transmit_sales_invoice(sales_invoice_name):
    """
    This function will check a transfer moe and transmit the invoice
    """

    #sales_invoice_name = "SI-BAL-22000003"
    #sales_order_name = "SO-BAL-22008129"

    #sales_invoice_name = "SI-BAL-22000005"
    #sales_order_name = "SO-BAL-22008108"

    sales_invoice_name = "SI-BAL-22000006"
    sales_order_name = "SO-BAL-22008238"


    sales_invoice = frappe.get_doc("Sales Invoice", sales_invoice_name)
    customer = frappe.get_doc("Customer", sales_invoice.customer)
    

    sales_order = frappe.get_doc("Sales Order", sales_order_name)
    #for k,v in sales_order.as_dict().items():
    #    print ( "%s: %s" %(k,v))

    # TODO: comment-in after development to handle invoice paths other than ariba
    
    if customer.invoicing_method == "Email":
        # send by mail
        target_email = customer.get("invoice_email") or sales_invoice.get("contact_email")
        if not target_email:
            frappe.log_error( "Unable to send {0}: no email address found.".format(sales_invoice_name), "Sending invoice email failed")
            return
        
        # TODO: send email with content & attachment
        
    elif customer.invoicing_method == "Post":
        # create and attach pdf
        execute({
            'doctype': 'Sales Invoice',
            'name': sales_invoice_name,
            'title': sales_invoice.title,
            'lang': sales_invoice.language,
            'print_format': "Sales Invoice",             # TODO: from configuration
            'is_private': 1
        })
        attachments = get_attachments("Communication", communication)
        fid = None
        for a in attachments:
            fid = a['name']
        # send mail to printer relais
        send(
            recipients="print@microsynth.local",        # TODO: config 
            subject=sales_invoice_name, 
            message=sales_invoice_name, 
            reference_doctype="Sales Invoice", 
            reference_name=sales_invoice_name,
            attachments=[{'fid': fid}]
        )
                
        pass

    elif customer.invoicing_method == "ARIBA":
        # create ARIBA cXML input data dict
        data = sales_invoice.as_dict()
        data['customer_record'] = customer.as_dict()
        cxml_data = create_dict_of_invoice_info_for_cxml(sales_invoice)

        cxml = frappe.render_template("microsynth/templates/includes/ariba_cxml.html", cxml_data)
        #print(cxml)

        # TODO: comment in after development to save ariba file to filesystem
        with open('/home/libracore/Desktop/'+ sales_invoice_name, 'w') as file:
            file.write(cxml)
        '''
        # attach to sales invoice
        folder = create_folder("ariba", "Home")
        # store EDI File  
    
        f = save_file(
            "{0}.txt".format(sales_invoice_name), 
            cxml, 
            "Sales Invoice", 
            sales_invoice_name, 
            folder = '/home/libracore/Desktop',
            # folder=folder, 
            is_private=True
        )
        '''

    elif customer.invoicing_method == "Paynet":
        # create Paynet cXML input data dict
        cxml_data = create_dict_of_invoice_info_for_cxml(sales_invoice)
        
        cxml = frappe.render_template("microsynth/templates/includes/paynet_cxml.html", cxml_data)
        #print(cxml)

        '''
        # TODO: comment in after development to save paynet file to filesystem
    
        # attach to sales invoice
        folder = create_folder("ariba", "Home")
        # store EDI File
        
        f = save_file(
            "{0}.txt".format(sales_invoice_name), 
            cxml, 
            "Sales Invoice", 
            sales_invoice_name, 
            folder=folder, 
            is_private=True
        )
        '''
    
    elif customer.invoicing_method == "GEP":
        print("IN GEP")
        # create Gep cXML input data dict
        cxml_data = create_dict_of_invoice_info_for_cxml(sales_invoice)
        cxml = frappe.render_template("microsynth/templates/includes/gep_cxml.html", cxml_data)
        print(cxml)

        '''
        # TODO: comment in after development to save gep file to filesystem
    
        # attach to sales invoice
        folder = create_folder("ariba", "Home")
        # store EDI File
        
        f = save_file(
            "{0}.txt".format(sales_invoice_name), 
            cxml, 
            "Sales Invoice", 
            sales_invoice_name, 
            folder=folder, 
            is_private=True
        )
        '''

        
    return
        
        
