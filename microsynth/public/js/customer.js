frappe.ui.form.on('Customer', {
    refresh(frm) {
        if ((!frm.doc.__islocal) && (frm.doc.default_price_list)) {
            frm.add_custom_button(__("Price List"), function() {
                frappe.set_route("query-report", "Pricing Configurator", {'price_list': frm.doc.default_price_list});
            });
        }
    }
});
