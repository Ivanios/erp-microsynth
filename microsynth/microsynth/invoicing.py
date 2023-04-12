# -*- coding: utf-8 -*-
# Copyright (c) 2022, libracore (https://www.libracore.com) and contributors
# For license information, please see license.txt
#
# For more details, refer to https://github.com/Microsynth/erp-microsynth/
#

import os
import traceback
import frappe
from frappe import _
from frappe.utils.background_jobs import enqueue
from microsynth.microsynth.report.invoiceable_services.invoiceable_services import get_data
from frappe.utils import cint
from erpnext.stock.doctype.delivery_note.delivery_note import make_sales_invoice
from erpnextswiss.erpnextswiss.attach_pdf import create_folder, execute
from frappe.utils.file_manager import save_file
from frappe.core.doctype.communication.email import make
from frappe.desk.form.load import get_attachments
from microsynth.microsynth.naming_series import get_naming_series
from microsynth.microsynth.utils import get_physical_path, get_billing_address, get_alternative_account, get_alternative_income_account, get_name, get_name_line, get_posting_datetime
from microsynth.microsynth.credits import allocate_credits, book_credit, get_total_credit
from microsynth.microsynth.jinja import get_destination_classification
import datetime
from datetime import datetime
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
    """
    run 
    bench execute microsynth.microsynth.invoicing.async_create_invoices --kwargs "{ 'mode':'Electronic', 'company': 'Microsynth AG' }"
    """

    all_invoiceable = get_data(filters={'company': company})

    # # Not implemented exceptions to catch cases that are not yet developed
    # if company != "Microsynth AG":
    #     frappe.throw("Not implemented: async_create_invoices for company '{0}'".format(company))
    #     return
    if mode not in ["Post", "Electronic", "Collective"]:
        frappe.throw("Not implemented: async_create_invoices for mode '{0}'".format(mode))
        return

    # Standard processing
    if (mode in ["Post", "Electronic"]):
        # individual invoices

        count = 0
        for dn in all_invoiceable:
            try:
                # # TODO: implement for other export categories
                # if dn.region != "CH":
                #     continue

                # TODO: implement for other product types. Requires setting the income accounts.
                # if dn.product_type not in ["Oligos", "Labels", "Sequencing"]:
                #     continue

                # process punchout orders separately
                if cint(dn.get('is_punchout') == 1):
                    # TODO implement punchout orders
                    # si = make_punchout_invoice(dn.get('delivery_note'))
                    # transmit_sales_invoice(si)
                    continue

                credit = get_total_credit(dn.get('customer'), company)
                if credit is not None and frappe.get_value("Customer", dn.get('customer'),"has_credit_account"):
                    delivery_note =  dn.get('delivery_note')
                    total = frappe.get_value("Delivery Note", delivery_note, "total")
                    if total > credit:
                        subject = "Delivery Note {0}: Insufficient credit".format(delivery_note)
                        message = "Delivery Note '{delivery_note}': Insufficient credit balance<br>Customer: {customer}<br>Total: {total} {currency}<br>Credit: {credit} {currency}".format(
                            delivery_note = delivery_note,
                            customer = dn.get('customer'),
                            total = total,
                            credit = round(credit, 2),
                            currency = dn.get('currency'))
                        
                        frappe.log_error(message.replace("<br>","\n"), "invocing.async_create_invoices")
                        print(message)
                        # make(
                        #     recipients = "info@microsynth.ch",
                        #     sender = "erp@microsynth.ch",
                        #     cc = "rolf.suter@microsynth.ch",
                        #     subject = subject,
                        #     content = message,
                        #     doctype = "Delivery Note",
                        #     name = delivery_note,
                        #     send_email = True
                        # )
                        continue

                # only process DN that are invoiced individually, not collective billing
                if cint(dn.get('collective_billing')) == 0:
                    if mode == "Post":
                        if dn.get('invoicing_method') == "Post":
                            si = make_invoice(dn.get('delivery_note'))
                            transmit_sales_invoice(si)

                            count += 1
                            # if count >= 20 and company != "Microsynth AG":
                            #     break

                    else:
                        # TODO process other invoicing methods
                        if dn.get('invoicing_method') not in  ["Email"]:
                            continue

                        # TODO there seems to be an issue here: both branches ("Post"/ not "Post") do the same
                        if dn.get('invoicing_method') != "Post":
                            si = make_invoice(dn.get('delivery_note'))
                            transmit_sales_invoice(si)
                            count += 1
                            # if count >= 20 and company != "Microsynth AG":
                            #     break
            except Exception as err:
                frappe.log_error("Cannot invoice {0}: \n{1}".format(dn.get('delivery_note'), err), "invoicing.async_create_invoices")

    elif mode == "Collective":
        # colletive invoices
        customers = []
        for dn in all_invoiceable:

            # TODO process other invoicing methods
            if dn.get('invoicing_method') not in  ["Email", "Post"]:
                continue

            if cint(dn.get('collective_billing')) == 1 and cint(dn.get('is_punchout')) != 1 and dn.get('customer') not in customers:
                customers.append(dn.get('customer'))

        # for each customer, create one invoice per tax template for all dns
        for c in customers:
            try:
                dns = []
                for dn in all_invoiceable:
                    if cint(dn.get('collective_billing')) == 1 and cint(dn.get('is_punchout')) != 1 and dn.get('customer') == c:
                        dns.append(dn.get('delivery_note'))

                if len(dns) > 0:
                    # check if there are multiple tax templates
                    taxes = []
                    for dn in dns:
                        t = frappe.db.get_value("Delivery Note", dn, "taxes_and_charges")
                        if t not in taxes:
                            taxes.append(t)

                    if len(taxes) > 1:
                        print("multiple taxes for customer '{0}'".format(c), "invocing.async_create_invoices")

                    credit = get_total_credit(c, company)

                    # create one invoice per tax template
                    for tax in taxes:
                        filtered_dns = []
                        for d in dns:
                            if frappe.db.get_value("Delivery Note", d, "taxes_and_charges") == tax:
                                total = frappe.get_value("Delivery Note", d, "total")

                                if credit is not None and frappe.get_value("Customer",c,"has_credit_account"):
                                    # there is some credit - check if it is sufficient
                                    if total <= credit:
                                        filtered_dns.append(d)
                                        credit = credit - total
                                    else:
                                        frappe.log_error("Delivery Note '{0}': \nInsufficient credit for customer {1}".format(d, c), "invocing.async_create_invoices")
                                else:
                                    # there is no credit account
                                    filtered_dns.append(d)

                        if len(filtered_dns) > 1:
                            si = make_collective_invoice(filtered_dns)
                            transmit_sales_invoice(si)
                            
            except Exception as err:
                frappe.log_error("Cannot create collective invoice for customer {0}: \n{1}".format(c, err), "invoicing.async_create_invoices")
    else:
        frappe.throw("Unknown mode '{0}' for async_create_invoices".format(mode))

    return


def set_income_accounts(sales_invoice):
    """
    Sets the income account for each item of a sales invoice based on the original income account entry and the country. 
    For the credit item, the alternative account is defined by the currency. Requires a sales invoice object as input.
    """
    if sales_invoice.shipping_address_name:
        address = sales_invoice.shipping_address_name
    else:
        address = sales_invoice.customer_address
    country = frappe.db.get_value("Address", address, "country")

    for item in sales_invoice.items:
        if item.item_code == "6100":
            # credit item
            item.income_account = get_alternative_account(item.income_account, sales_invoice.currency)
        else:
            # all other items
            item.income_account = get_alternative_income_account(item.income_account, country)
    sales_invoice.save()


def make_invoice(delivery_note):
    """
    Includes customer credits. Do not use for customer projects.

    run
    bench execute microsynth.microsynth.invoicing.make_invoice --kwargs "{'delivery_note':'DN-BAL-23106510'}"
    """
    sales_invoice_content = make_sales_invoice(delivery_note)
    # compile document
    sales_invoice = frappe.get_doc(sales_invoice_content)
    company = frappe.get_value("Delivery Note", delivery_note, "company")
    sales_invoice.naming_series = get_naming_series("Sales Invoice", company)
    if not sales_invoice.invoice_to:
        sales_invoice.invoice_to = frappe.get_value("Customer", sales_invoice.customer, "invoice_to") # replace contact with customer's invoice_to contact
    #sales_invoice.set_advances()    # get advances (customer credit)
    sales_invoice = allocate_credits(sales_invoice)         # check and allocated open customer credits
    
    # force-set tax_id (intrastat!)
    if not sales_invoice.tax_id:
        sales_invoice.tax_id = frappe.get_value("Customer", sales_invoice.customer, "tax_id")
    
    sales_invoice.insert()
    set_income_accounts(sales_invoice)
    sales_invoice.submit()
    # if a credit was allocated, book credit account
    if cint(sales_invoice.total_customer_credit) > 0:
        book_credit(sales_invoice.name)
        
    frappe.db.commit()

    return sales_invoice.name


def make_punchout_invoice(delivery_note):
    """
    Create an invoice for a delivery note of a punchout order. Returns the sales invoice ID.

    run 
    bench execute microsynth.microsynth.invoicing.make_punchout_invoice --kwargs "{'delivery_note':'DN-BAL-23112515'}"
    """

    delivery_note = frappe.get_doc("Delivery Note", delivery_note)

    # get Sales Order to fetch punchout data not saved to the delivery note
    # TODO: remove fetching sales order once all delivery notes have the punchout shop
    sales_orders = []
    for x in delivery_note.items:
        if x.against_sales_order is not None and x.against_sales_order not in sales_orders:
            sales_orders.append(x.against_sales_order)
    
    if len(sales_orders) == 1:
        sales_order = frappe.get_doc("Sales Order", sales_orders[0])
    else:
        frappe.log_error("The delivery note '{0}' originates from none or multiple sales orders".format(delivery_note.name), "invoicing.make_punchout_invoice")
        return None
    
    # set the punchout shop
    if delivery_note.punchout_shop is not None:
        punchout_shop = frappe.get_doc("Punchout Shop", delivery_note.punchout_shop)
    elif sales_order.punchout_shop is not None:
        punchout_shop = frappe.get_doc("Punchout Shop", sales_order.punchout_shop)
    else:
        frappe.log_error("Cannot invoice delivery note '{0}': Punchout Shop is not defined".format(delivery_note.name), "invoicing.make_punchout_invoice")
        return None

    sales_invoice_content = make_sales_invoice(delivery_note.name)

    # compile document
    sales_invoice = frappe.get_doc(sales_invoice_content)
    company = frappe.get_value("Delivery Note", delivery_note.name, "company")
    sales_invoice.naming_series = get_naming_series("Sales Invoice", company)
    
    if punchout_shop.has_static_billing_address and punchout_shop.billing_contact: 
        sales_invoice.invoice_to = punchout_shop.billing_contact

    if punchout_shop.has_static_billing_address and punchout_shop.billing_address:
        sales_invoice.customer_address = punchout_shop.billing_address

    # force-set tax_id (intrastat!)
    if not sales_invoice.tax_id:
        sales_invoice.tax_id = frappe.get_value("Customer", sales_invoice.customer, "tax_id")

    sales_invoice.insert()
    set_income_accounts(sales_invoice)
    sales_invoice.submit()
    frappe.db.commit()

    return sales_invoice.name


def make_collective_invoice(delivery_notes):
    """
    
    run
    bench execute microsynth.microsynth.invoicing.make_collective_invoice --kwargs "{'delivery_notes': ['DN-BAL-23106590', 'DN-BAL-23113391', 'DN-BAL-23114506', 'DN-BAL-23115682']}"
    """

    # create invoice from first delivery note
    sales_invoice_content = make_sales_invoice(delivery_notes[0])
    if len(delivery_notes) > 1:
        for i in range(1, len(delivery_notes)):
            # append items from other delivery notes
            sales_invoice_content = make_sales_invoice(source_name=delivery_notes[i], target_doc=sales_invoice_content)
    
    # compile document
    sales_invoice = frappe.get_doc(sales_invoice_content)
    if not sales_invoice.invoice_to:
        sales_invoice.invoice_to = frappe.get_value("Customer", sales_invoice.customer, "invoice_to") # replace contact with customer's invoice_to contact

    company = frappe.get_value("Delivery Note", delivery_notes[0], "company")
    sales_invoice.naming_series = get_naming_series("Sales Invoice", company)
        
    # sales_invoice.set_advances()    # get advances (customer credit)
    sales_invoice = allocate_credits(sales_invoice)         # check and allocated open customer credits

    # force-set tax_id (intrastat!)
    if not sales_invoice.tax_id:
        sales_invoice.tax_id = frappe.get_value("Customer", sales_invoice.customer, "tax_id")

    sales_invoice.insert()
    set_income_accounts(sales_invoice)
    sales_invoice.submit()

    # if a credit was allocated, book credit account
    if cint(sales_invoice.total_customer_credit) > 0:
        book_credit(sales_invoice.name)

    frappe.db.commit()

    return sales_invoice.name


def create_pdf_attachment(sales_invoice): 
    """
    Creates the PDF file for a given Sales Invoice name and attaches the file to the record in the ERP.

    run
    bench execute microsynth.microsynth.utils.create_pdf_attachment --kwargs "{'sales_invoice': 'SI-BAL-23002642-1'}"
    """

    doctype = "Sales Invoice"
    format = "Sales Invoice"
    name = sales_invoice
    doc = None
    no_letterhead = False
    
    frappe.local.lang = frappe.db.get_value("Sales Invoice", sales_invoice, "language")

    from erpnextswiss.erpnextswiss.attach_pdf import save_and_attach, create_folder

    title = frappe.db.get_value(doctype, name, "title")

    doctype_folder = create_folder(doctype, "Home")
    title_folder = create_folder(title, doctype_folder)

    filecontent = frappe.get_print(doctype, name, format, doc=doc, as_pdf = True, no_letterhead=no_letterhead)

    save_and_attach(
        content = filecontent, 
        to_doctype = doctype, 
        to_name = name,  
        folder = title_folder,
        hashname = None,
        is_private = True )

    return


def get_sales_order_list_and_delivery_note_list(sales_invoice): 
    """creates a dict with two keys sales_orders/delivery_notes with value of a list of respective ids"""

    sales_order_list = []
    delivery_note_list = []

    for item in sales_invoice.items:
        if item.sales_order and item.sales_order not in sales_order_list: 
            sales_order_list.append(item.sales_order)
        if item.delivery_note and item.delivery_note not in delivery_note_list: 
            delivery_note_list.append(item.delivery_note)

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

    # need a dict of all item to predict price of an oligo artice
    all_sole_items = {}
    item_list = sales_invoice.items
    for item in item_list:
        all_sole_items[item.item_code] = item 
        #print ("\n")
        #for k, v in item.as_dict().items():
        #    print ("{}: {}".format(k, v))

    # oligo article
    invoiced_oligos = {}
    for oligo_link in sales_invoice.oligos: 
        invoice_position += 1 
        oligo_object = frappe.get_doc("Oligo", oligo_link.oligo)
        oligo_details = {}
        oligo_details["oligo_article"] = oligo_object
        oligo_details["invoice_position"] = invoice_position
        oligo_details["quantity"] = 1
        oligo_details["description"] = oligo_object.oligo_name
        oligo_details["price"] = 0
        oligo_details["base_price"] = 0
        for oligo_item in oligo_object.items:
            oligo_details["price"] += oligo_item.qty * all_sole_items[oligo_item.item_code].rate
            oligo_details["base_price"] = oligo_item.qty * all_sole_items[oligo_item.item_code].base_rate
        list_of_invoiced_items.append(oligo_details)
        #print ("\n")
        #for k, v in oligo_object.as_dict().items():
        #    print ("{}: {}".format(k, v))

    # other articles incl shipping 
    for item in sales_invoice.items:
        invoice_item_dicts[item.item_code] = item
        if item.item_group not in ["3.1 DNA/RNA Synthese", "Shipping"]: 
            for k, v in item.as_dict().items(): 
                print ("{}: {}".format(k, v))
            # other items (labels)
            invoice_other_items = {}
            invoice_position += 1
            invoice_other_items["other_article"] = item
            invoice_other_items["invoice_position"] = invoice_position
            invoice_other_items["quantity"] = item.qty
            invoice_other_items["description"] = item.item_name
            invoice_other_items["price"] = item.rate
            invoice_other_items["base_price"] = item.base_rate
            list_of_invoiced_items.append(invoice_other_items)

        elif item.item_group == "Shipping": 
            # shipping
            invoice_position += 1
            invoiced_shipping = {}
            invoiced_shipping["shipping_article"] = item
            invoiced_shipping["invoice_position"] = invoice_position
            invoiced_shipping["quantity"] = 1
            invoiced_shipping["description"] = item.item_name
            invoiced_shipping["price"] = item.amount
            invoiced_shipping["base_price"] = item.base_amount
            list_of_invoiced_items.append(invoiced_shipping)
    
    return list_of_invoiced_items

def get_address_dict(customer, contact, address, country_codes):
    
    postal_address = {}
    deliver_to = []

    name = get_name(contact)

    if name != "":
        deliver_to.append(get_name(contact))

    if contact.department:
        deliver_to.append(contact.department)

    if contact.institute:
        deliver_to.append(contact.institute)

    if contact.room:
        deliver_to.append(contact.room)
    
    postal_address["id"] = address.customer_address_id
    postal_address["name"] = address.overwrite_company or customer
    postal_address["deliver_to"] = deliver_to
    postal_address["street1"] = address.address_line1
    postal_address["street2"] = address.address_line2
    postal_address["pin"] = address.pincode
    postal_address["city"] = address.city
    postal_address["country_code"] = country_codes[address.country].upper()
    
    return postal_address


def create_position_list(sales_invoice, exclude_shipping):
    """
    Create a list of the invoice positions of a sales_invoice as a list of dictionaries.
    """
    item_details = {}
    
    for item in sales_invoice.items:
        item_details[item.item_code] = item

    positions = []
    number = 0
    used_items = {}

    tax_rate = sales_invoice.taxes[0].rate if len(sales_invoice.taxes)>0 else 0

    for o in sales_invoice.oligos:
        position = {}
        number += 1
        rate_total = 0
        oligo = frappe.get_doc("Oligo", o.oligo)

        for n in oligo.items:
            if n.item_code in item_details:
                rate_total += n.qty * item_details[n.item_code].rate

            if n.item_code not in used_items:
                used_items[n.item_code] = n.qty
            else:
                used_items[n.item_code] = used_items[n.item_code] + n.qty

        position["number"] = number
        position["item"] = "{0}-{1}".format(sales_invoice.web_order_id, oligo.web_id)
        position["description"] = oligo.oligo_name
        position["quantity"] = 1
        position["rate"] = rate_total
        position["amount"] = rate_total
        position["tax_amount"] = tax_rate * rate_total / 100
        positions.append(position)

    # TODO Implement for samples

    for n in sales_invoice.items:
        if n.item_group == "Shipping" and exclude_shipping:
            continue
        else:
            if n.item_code not in used_items:
                position = {}

                if number > 0:
                    number += 1
                    position["number"] = number
                else:
                    position["number"] = n.idx

                position["item"] = n.item_code
                position["description"] = n.description
                position["quantity"] = n.qty
                position["rate"] = n.rate
                position["amount"] = n.amount
                position["tax_amount"] = tax_rate * n.amount / 100
                positions.append(position)

            elif n.qty > used_items[n.item_code]:
                # more items in positions than used in oligos and samples
                position = {}

                if number > 0:
                    number += 1
                    position["number"] = number
                else:
                    position["number"] = n.idx

                position["item"] = n.item_code
                position["description"] = n.description
                position["quantity"] = n.qty - used_items[n.item_code]
                position["rate"] = n.rate
                position["amount"] = n.amount
                position["tax_amount"] = tax_rate * n.amount / 100
                positions.append(position)

    return positions


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


def create_dict_of_invoice_info_for_cxml(sales_invoice, mode): 
    """ Doc string """

    shipping_address = frappe.get_doc("Address", sales_invoice.shipping_address_name)
    shipping_contact = frappe.get_doc("Contact", sales_invoice.shipping_contact)

    customer = frappe.get_doc("Customer", sales_invoice.customer)
    company_details = frappe.get_doc("Company", sales_invoice.company)
    company_address = frappe.get_doc("Address", sales_invoice.company_address)
    # customer_contact = frappe.get_doc("Contact", sales_invoice.contact_person)

    
    settings = frappe.get_doc("Microsynth Settings", "Microsynth Settings")

    if sales_invoice.is_punchout:
        punchout_shop = frappe.get_doc("Punchout Shop", sales_invoice.punchout_shop)

    # define billing address
    if sales_invoice.is_punchout and punchout_shop.has_static_billing_address:
        billing_address = frappe.get_doc("Address", punchout_shop.billing_address)
        billing_contact = frappe.get_doc("Contact", punchout_shop.billing_contact)
    else:
        billing_address = frappe.get_doc("Address", sales_invoice.customer_address)
        billing_contact = frappe.get_doc("Contact", sales_invoice.invoice_to)
    
    # define shipping costs on header/item level
    shipping_costs = 0
    if mode == "ARIBA" or mode == "GEP":
        # TODO handle non-punchout invoices for Ariba/GEP
        shipping_as_item = punchout_shop.cxml_shipping_as_item

        for n in sales_invoice.items:
            if n.item_group == "Shipping" and not shipping_as_item:
                shipping_costs += n.amount
    else: 
        shipping_as_item = True

    # other data
    bank_account = frappe.get_doc("Account", sales_invoice.debit_to)
    tax_rate = sales_invoice.taxes[0].rate if len(sales_invoice.taxes) > 0 else 0

    country_codes = create_country_name_to_code_dict()
    itemList = create_list_of_item_dicts_for_cxml(sales_invoice)

    posting_timepoint = get_posting_datetime(sales_invoice)

    ship_to_address = get_address_dict(
        customer = sales_invoice.customer_name,
        contact = shipping_contact,
        address = shipping_address,
        country_codes = country_codes)
    
    bill_to_address = get_address_dict(
        customer = sales_invoice.customer_name,
        contact = billing_contact,
        address = billing_address,
        country_codes = country_codes)

    data2 = {'basics' : {'sender_network_id' :  settings.ariba_id,
                        'receiver_network_id':  customer.invoice_network_id,
                        'shared_secret':        settings.ariba_secret,
                        'paynet_sender_pid':    settings.paynet_id, 
                        'payload_id':           posting_timepoint.strftime("%Y%m%d%H%M%S") + str(random.randint(0, 10000000)) + "@microsynth.ch",
                        'timestamp':            datetime.now().strftime("%Y-%m-%dT%H:%M:%S+01:00"),
                        'order_id':             sales_invoice.po_no, 
                        'currency':             sales_invoice.currency,
                        'invoice_id':           sales_invoice.name,
                        'invoice_date':         posting_timepoint.strftime("%Y-%m-%dT%H:%M:%S+01:00"),
                        'invoice_date_paynet':  posting_timepoint.strftime("%Y%m%d"),
                        'delivery_note_id':     sales_invoice.items[0].delivery_note, 
                        'delivery_note_date_paynet':  "" # delivery_note.creation.strftime("%Y%m%d"),
                        },
            'remitTo' : {'name':            sales_invoice.company,
                        'street':           company_address.address_line1, 
                        'pin':              company_address.pincode,
                        'city':             company_address.city, 
                        'iso_country_code': country_codes[company_address.country].upper(), 
                        'supplier_tax_id':  company_details.tax_id
                        },
            'billTo' : {'address':          bill_to_address
                        },
            'from' :    {'name':            company_details.company_name,
                        'street':           company_address.address_line1, 
                        'pin':              company_address.pincode,
                        'city':             company_address.city,
                        'iso_country_code': country_codes[company_address.country].upper()
                        }, 
            'soldTo' :  {'address':         bill_to_address
                        }, 
            'shipFrom' : {'name':           company_details.name, 
                        'street':           company_address.address_line1,
                        'pin':              company_address.pincode,
                        'city':             company_address.city,
                        'iso_country_code': country_codes[company_address.country].upper()
                        },
            'shipTo' : {'address':          ship_to_address
                        }, 
            # 'contact':  {'full_name':       invoice_contact.full_name, 
            #             'department':       customer_contact.department,
            #             'room':             customer_contact.room,
            #             'institute':        customer_contact.institute
            #             },
            'receivingBank' : {'swift_id':  bank_account.bic,
                        'iban_id':          bank_account.iban,
                        'account_name':     bank_account.company,
                        'account_id':       bank_account.iban,
                        'account_type':     'Checking',  
                        'branch_name':      bank_account.bank_name + " " + bank_account.bank_branch_name if bank_account.bank_branch_name else bank_account.bank_name
                        }, 
            'extrinsic' : {'buyerVatId':                customer.tax_id,
                        'supplierVatId':                company_details.tax_id,
                        'supplierCommercialIdentifier': company_details.tax_id
                        }, 
            'items' :   itemList,
            'positions': create_position_list(sales_invoice = sales_invoice, exclude_shipping = not punchout_shop.cxml_shipping_as_item),
            'tax' :     {'amount' :         sales_invoice.total_taxes_and_charges,
                        'taxable_amount' :  sales_invoice.net_total,
                        'percent' :         tax_rate, 
                        'taxPointDate' :    posting_timepoint.strftime("%Y-%m-%dT%H:%M:%S+01:00"),
                        'description' :     sales_invoice.taxes[0].description if len(sales_invoice.taxes)>0 else 0
                        },
            
            # shipping for Ariba is listed on header level, shipping for GEP is listed on item level
            'shippingTax' : {'taxable_amount':  shipping_costs,
                        'amount':               shipping_costs * tax_rate / 100,
                        'percent':              tax_rate,
                        'taxPointDate':         posting_timepoint.strftime("%Y-%m-%dT%H:%M:%S+01:00"),
                        'description' :         "{0}% shipping tax".format(tax_rate)
                        }, 
            'summary' : {'subtotal_amount' :        sales_invoice.base_total,
                        'shipping_amount' :         shipping_costs,
                        'gross_amount' :            sales_invoice.rounded_total,
                        'total_amount_without_tax': sales_invoice.net_total,
                        'net_amount' :              sales_invoice.rounded_total,
                        'due_amount' :              sales_invoice.rounded_total
                        }
            }
    return data2


def transmit_sales_invoice(sales_invoice):
    """
    This function will check the transfer mode and transmit the invoice

    run
    bench execute microsynth.microsynth.invoicing.transmit_sales_invoice --kwargs "{'sales_invoice':'SI-BAL-23001808'}"
    """

    try:
        sales_invoice = frappe.get_doc("Sales Invoice", sales_invoice)
        customer = frappe.get_doc("Customer", sales_invoice.customer)
        
        if sales_invoice.invoice_to:
            invoice_contact = frappe.get_doc("Contact", sales_invoice.invoice_to)
        else:
            invoice_contact = frappe.get_doc("Contact", sales_invoice.contact_person)
        #for k,v in sales_order.as_dict().items():
        #    print ( "%s: %s" %(k,v))

        # TODO: comment-in after development to handle invoice paths other than ariba
        
        # The invoice was already sent. Do not send again.
        # if sales_invoice.invoice_sent_on:
        #     print("Invoice '{0}' was already sent on: {1}".format(sales_invoice.name, sales_invoice.invoice_sent_on))
        #     return

        # Do not send any invoice if the items are free of charge
        if sales_invoice.total == 0:
            return

        if sales_invoice.is_punchout:
            mode = frappe.get_value("Punchout Shop", sales_invoice.punchout_shop, "invoicing_method")
        else:
            if customer.invoicing_method == "Post":
                # Send all invoices with credit account per mail
                if sales_invoice.net_total == 0:
                    mode = "Email"
                else:
                    mode = "Post"
            elif customer.invoicing_method == "Email":
                mode = "Email"
            elif customer.invoicing_method == "ARIBA":
                mode = "ARIBA"
            elif customer.invoicing_method == "Paynet":
                mode = "Paynet"
            elif customer.invoicing_method == "GEP":
                mode = "GEP"
            else:
                mode = None

        print("Transmission mode for Sales Invoice '{0}': {1}".format(sales_invoice.name, mode))

        if mode == "Email":
            # send by mail

            # TODO check sales_invoice.invoice_to --> if it has a e-mail --> this is target-email

            target_email = invoice_contact.email_id
            if not target_email:
                frappe.log_error( "Unable to send {0}: no email address found.".format(sales_invoice.name), "Sending invoice email failed")
                return

            if sales_invoice.company == "Microsynth AG":
                destination = get_destination_classification(si = sales_invoice.name)

                if destination == "EU":
                    footer =  frappe.get_value("Letter Head", "Microsynth AG Wolfurt", "footer")
                else:
                    footer =  frappe.get_value("Letter Head", sales_invoice.company, "footer")
            else: 
                footer = frappe.get_value("Letter Head", sales_invoice.company, "footer")

            create_pdf_attachment(sales_invoice.name)

            attachments = get_attachments("Sales Invoice", sales_invoice.name)
            fid = None
            for a in attachments:
                fid = a['name']
            frappe.db.commit()

            if sales_invoice.language == "de":
                subject = "Rechnung {0}".format(sales_invoice.name)
                message = "Sehr geehrter Kunde<br>Bitte beachten Sie die angehängte Rechnung '{0}'.<br>Beste Grüsse<br>Administration<br><br>{1}".format(sales_invoice.name, footer)
            elif sales_invoice.language == "fr":
                subject = "Facture {0}".format(sales_invoice.name)
                message = "Cher client<br>Veuillez consulter la facture ci-jointe '{0}'.<br>Meilleures salutations<br>Administration<br><br>{1}".format(sales_invoice.name, footer)
            else:
                subject = "Invoice {0}".format(sales_invoice.name)
                message = "Dear Customer<br>Please find attached the invoice '{0}'.<br>Best regards<br>Administration<br><br>{1}".format(sales_invoice.name, footer)

            make(
                recipients = target_email,
                sender = "info@microsynth.ch",
                cc = "info@microsynth.ch",
                subject = subject, 
                content = message,
                doctype = "Sales Invoice",
                name = sales_invoice.name,
                attachments = [{'fid': fid}],
                send_email = True
            )

        elif mode == "Post":
            create_pdf_attachment(sales_invoice.name)

            attachments = get_attachments("Sales Invoice", sales_invoice.name)
            fid = None
            for a in attachments:
                fid = a['name']
            frappe.db.commit()
            
            # print the pdf with cups
            path = get_physical_path(fid)
            PRINTER = frappe.get_value("Microsynth Settings", "Microsynth Settings", "invoice_printer")
            import subprocess
            subprocess.run(["lp", path, "-d", PRINTER])

            pass

        elif mode == "ARIBA":
            # create ARIBA cXML input data dict
            cxml_data = create_dict_of_invoice_info_for_cxml(sales_invoice, mode)

            cxml = frappe.render_template("microsynth/templates/includes/ariba_cxml.html", cxml_data)
            #print(cxml)

            # TODO: comment in after development to save ariba file to filesystem
            with open('/home/libracore/Desktop/'+ sales_invoice.name, 'w') as file:
                file.write(cxml)
            '''
            # attach to sales invoice
            folder = create_folder("ariba", "Home")
            # store EDI File  
        
            f = save_file(
                "{0}.txt".format(sales_invoice.name), 
                cxml, 
                "Sales Invoice", 
                sales_invoice.name, 
                folder = '/home/libracore/Desktop',
                # folder=folder, 
                is_private=True
            )
            '''

        elif mode == "Paynet":
            # create Paynet cXML input data dict
            cxml_data = create_dict_of_invoice_info_for_cxml(sales_invoice, mode)
            
            cxml = frappe.render_template("microsynth/templates/includes/paynet_cxml.html", cxml_data)
            #print(cxml)

            # TODO: comment in after development to save ariba file to filesystem
            with open('/home/libracore/Desktop/'+ sales_invoice.name, 'w') as file:
                file.write(cxml)

            '''
            # TODO: comment in after development to save paynet file to filesystem
        
            # attach to sales invoice
            folder = create_folder("ariba", "Home")
            # store EDI File
            
            f = save_file(
                "{0}.txt".format(sales_invoice.name), 
                cxml, 
                "Sales Invoice", 
                sales_invoice.name, 
                folder=folder, 
                is_private=True
            )
            '''
        
        elif mode == "GEP":
            print("IN GEP")
            # create Gep cXML input data dict
            cxml_data = create_dict_of_invoice_info_for_cxml(sales_invoice, mode)
            cxml = frappe.render_template("microsynth/templates/includes/gep_cxml.html", cxml_data)
            file = open('/home/libracore/Desktop/'+ sales_invoice.name, 'w')
            file.write(cxml)
            file.close()
            '''
            # TODO: comment in after development to save gep file to filesystem
        
            # attach to sales invoice
            folder = create_folder("ariba", "Home")
            # store EDI File
            
            f = save_file(
                "{0}.txt".format(sales_invoice.name), 
                cxml, 
                "Sales Invoice", 
                sales_invoice.name, 
                folder=folder, 
                is_private=True
            )
            '''

        else:
            return
        
        # sales_invoice.invoice_sent_on = datetime.now()
        # sales_invoice.save()
        frappe.db.set_value("Sales Invoice", sales_invoice.name, "invoice_sent_on", datetime.now(), update_modified = False)

        frappe.db.commit()

    except Exception as err:
        frappe.log_error("Cannot transmit sales invoice {0}: \n{1}\n{2}".format(
            sales_invoice.name, 
            err,
            traceback.format_exc()), "invoicing.transmit_sales_invoice")

    return


def pdf_export(sales_invoices, path):
    for sales_invoice in sales_invoices:
        content_pdf = frappe.get_print(
            "Sales Invoice", 
            sales_invoice, 
            print_format="Sales Invoice", 
            as_pdf=True)
        file_name = "{0}/{1}.pdf".format(path, sales_invoice)
        with open(file_name, mode='wb') as file:
            file.write(content_pdf)


def transmit_carlo_erba_invoices(company):
    """
    run
    bench execute microsynth.microsynth.invoicing.transmit_carlo_erba_invoices --kwargs "{'company': 'Microsynth Seqlab GmbH'}"
    """

    query = """
        SELECT `tabSales Invoice`.`name`
        FROM `tabSales Invoice`
        LEFT JOIN `tabCustomer` ON `tabSales Invoice`.`customer` = `tabCustomer`.`name`
        WHERE `tabSales Invoice`.`company` = "{company}"
        AND `tabCustomer`.`invoicing_method` = "Carlo ERBA"
        AND `tabSales Invoice`.`docstatus` <> 2
        AND `tabSales Invoice`.`status` <> "Paid"
        AND `tabSales Invoice`.`outstanding_amount` > 0
        AND `tabSales Invoice`.`invoice_sent_on` is NULL
    """.format(company=company)

    invoices = frappe.db.sql(query, as_dict=True)
    invoice_names = []
    for i in invoices:
        if "SI-OP-" in i.name:
            continue
        print(i.name)
        invoice_names.append(i.name)

    path = frappe.get_value("Microsynth Settings", "Microsynth Settings", "carlo_erba_export_path") + "/" + datetime.now().strftime("%Y-%m-%d__%H-%M")
    if not os.path.exists(path):
        os.mkdir(path)

    pdf_export(invoice_names, path)

    lines = []

    for invoice_name in invoice_names:
        si = frappe.get_doc("Sales Invoice", invoice_name)

        # Cliente (sold-to-party)
        # Billing address of the order customer
        order_customer_id = si.order_customer if si.order_customer else si.customer
        order_contact_id = frappe.db.get_value("Customer", order_customer_id, "invoice_to")

        order_customer = si.order_customer_display if si.order_customer_display else si.customer_name
        order_contact = frappe.get_doc("Contact", order_contact_id)
        order_address = get_billing_address(order_customer_id)

        # Acquiren (ship-to-party)
        shipping_customer = si.order_customer_display if si.order_customer_display else si.customer_name
        shipping_contact = frappe.get_doc("Contact", si.shipping_contact if si.shipping_contact else si.contact_person)
        shipping_address = frappe.get_doc("Address", si.shipping_address_name)

        # Billing (bill-to-party)
        # Billing address of Carlo Erba who needs to pay the invoice
        billing_customer = si.customer_name
        billing_contact = frappe.get_doc("Contact", si.invoice_to)
        billing_address = frappe.get_doc("Address", si.customer_address)

        # First delivery note
        delivery_note = si.items[0].delivery_note
        delivery_date = datetime.combine(
            frappe.get_value("Delivery Note", delivery_note, "posting_date"), 
            (datetime.min + frappe.get_value("Delivery Note", delivery_note, "posting_time")).time())

        # Header
        header = [
            "Header",                                                                       # record_type(8)
            si.web_order_id,                                                                # sales_order_number(8)
            si.name,                                                                        # invoice_number(8)
            si.po_no if si.po_no else "",                                                   # customer_po_number(22)
            si.posting_date.strftime("%d.%m.%Y"),                                           # invoice_date(8)
            delivery_date.strftime("%d.%m.%Y"),                                             # shipping_date(8)    // use first Delivery Note
            si.customer,                                                                    # customer_number(8)
            shipping_contact.name,                                                          # shipping_number(8)
            billing_contact.name,                                                           # bill_to_number(8)
            delivery_note,                                                                  # delivery_number(30) // use first Delivery Note
            str(len(si.items)),                                                             # trailer_amount(8)   // number of positions?
            str(si.total),                                                                  # netto_amount(15)
            str(si.grand_total) ]                                                           # total_amount(15)

        lines.append(header)

        # Addresses
        def get_address_data(type, customer_name, contact, address):
            data = [
                type,                                                                       # record_type(8)
                si.web_order_id,                                                            # sales_order_number(8)
                si.name,                                                                    # invoice_number(8)
                contact.name,                                                               # customer_number(8)
                contact.designation if contact.designation else "",                         # titel(8)
                get_name(contact),                                                          # name(60)
                address.overwrite_company if address.overwrite_company else customer_name,  # adress1(60)
                contact.department if contact.department else "",                           # adress2(60)
                address.address_line1 if address.address_line1 else "",                     # adress3(51)
                (frappe.get_value("Country", address.country, "code")).upper(),             # country_code(2)
                address.pincode if address.pincode else "",                                 # postal_code(10)
                address.city if address.city else "",                                       # city(20)
                get_name(contact),                                                          # contact_person(24)
                contact.email_id if contact.email_id else "",                               # email(40)
                contact.phone if contact.phone else "",                                     # phone_number(20)
                "",                                                                         # fax_number(20)
            ]
            return data

        # Sold-to-party
        client = get_address_data(
            type = "Cliente",
            customer_name = order_customer,
            contact = order_contact,
            address = order_address)

        # Ship-to-party
        shipping = get_address_data(
            type = "Acquiren",
            customer_name = shipping_customer,
            contact = shipping_contact,
            address = shipping_address)

        # bill-to-party
        billing = get_address_data(
            type = "Billing",
            customer_name = billing_customer,
            contact = billing_contact,
            address = billing_address)

        lines.append(client)
        lines.append(shipping)
        lines.append(billing)

        # Comments
                                                                                            # record_type(8)
                                                                                            # sales_order_number(8)
                                                                                            # invoice_number(8)
                                                                                            # comments(76)

        # Position
        i = 1
        for item in si.items:
            if item.amount == 0:
                continue
            position = [
                "Pos",                                                                      # record_type(8)
                si.web_order_id,                                                            # sales_order_number(8)
                si.name,                                                                    # invoice_number(8)
                str(i),                                                                     # position_line(3)
                item.item_code,                                                             # kit_item(18)
                str(item.qty),                                                              # kit_quantity(17)
                str(item.rate),                                                             # list_price(17)
                "0",                                                                        # discount_percent(17)
                str(item.amount),                                                           # kit_price(17)
                "",                                                                         # serial_number(24)
                item.description,                                                           # description1(24)
                ""                                                                          # description2(24)
            ]
            lines.append(position)
            i += 1

        # Components
                                                                                            # record_type(8)
                                                                                            # sales_order_number(8)
                                                                                            # invoice_number(8)
                                                                                            # position_line(3)
                                                                                            # component_number(18)
                                                                                            # component_quantity(17)
                                                                                            # component_price(17)
                                                                                            # component_feature(12)
                                                                                            # description1(24)
                                                                                            # description2(24)

    text = "\r\n".join( [ "\t".join(line) for line in lines ] )

    file = open(path + "/export.txt", "w")
    file.write(text)
    file.close()

    return
