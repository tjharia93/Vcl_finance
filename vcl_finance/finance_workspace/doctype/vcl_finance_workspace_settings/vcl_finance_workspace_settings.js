frappe.ui.form.on('VCL Finance Workspace Settings', {
  refresh(frm) {
    frm.disable_save();
    frm.set_df_property('final_approver', 'read_only', 1);
    if (!frappe.user_roles.includes('System Manager') || frappe.session.user !== frm.doc.final_approver) return;
    frm.add_custom_button(__('Apply release configuration'), () => {
      if (!frm.doc.release_attestation?.trim()) {
        frappe.msgprint(__('Record the acceptance evidence before changing a release.'));
        return;
      }
      let rules, gaps;
      try {
        rules = JSON.parse(frm.doc.dimension_rules_json || '[]');
        gaps = JSON.parse(frm.doc.reporting_gaps_json || '[]');
      } catch {
        frappe.msgprint(__('Dimension rules and reporting gaps must contain valid JSON.'));
        return;
      }
      frappe.confirm(__('Apply these phase switches and policy gates? The recorded acceptance evidence will be retained.'), () => {
        frappe.call({
          method: 'vcl_finance.workspace.api.configure_release',
          type: 'POST', freeze: true,
          args: {
            company: frappe.defaults.get_user_default('Company') || 'Vimit Converters Limited',
            phases: {1: !!frm.doc.phase_1, 2: !!frm.doc.phase_2, 3: !!frm.doc.phase_3, 4: !!frm.doc.phase_4},
            journal_review_required: frm.doc.journal_review_required || 0,
            sync_respects_locks: frm.doc.sync_respects_locks || 0,
            attestation: frm.doc.release_attestation,
            dimension_rules: rules, reporting_gaps: gaps
          },
          callback: () => {frm.reload_doc(); frappe.show_alert({message: __('Release configuration applied'), indicator: 'green'});}
        });
      });
    }).addClass('btn-primary');
  }
});
