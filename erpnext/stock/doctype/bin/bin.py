# Please edit this list and import only required elements
import webnotes

from webnotes.utils import cint, cstr, flt
from webnotes.model.code import get_obj
from webnotes import msgprint

sql = webnotes.conn.sql

# -----------------------------------------------------------------------------------------


class DocType:	
	def __init__(self, doc, doclist=[]):
		self.doc = doc
		self.doclist = doclist

	# -------------
	# stock update
	# -------------
	def update_stock(self, actual_qty=0, reserved_qty=0, ordered_qty=0, indented_qty=0, planned_qty=0, dt=None, sle_id='', posting_time='', serial_no = '', is_cancelled = 'No'):

		if not dt: dt = nowdate()
		# update the stock values (for current quantities)
		self.doc.actual_qty = flt(self.doc.actual_qty) + flt(actual_qty)
		self.doc.ordered_qty = flt(self.doc.ordered_qty) + flt(ordered_qty)
		self.doc.reserved_qty = flt(self.doc.reserved_qty) + flt(reserved_qty)
		self.doc.indented_qty = flt(self.doc.indented_qty) + flt(indented_qty)
		self.doc.planned_qty = flt(self.doc.planned_qty) + flt(planned_qty)
		self.doc.projected_qty = flt(self.doc.actual_qty) + flt(self.doc.ordered_qty) + flt(self.doc.indented_qty) + flt(self.doc.planned_qty) - flt(self.doc.reserved_qty)

		self.doc.save()
			
		
		# update valuation for post dated entry
		if actual_qty:
			# check actual qty with total number of serial no
			if serial_no:
				self.check_qty_with_serial_no()
			
			self.update_entries_after(dt, posting_time)

	def check_qty_with_serial_no(self):
		"""
			check actual qty with total number of serial no in store
			Temporary validation added on: 18-07-2011
		"""
		if sql("select name from `tabItem` where ifnull(has_serial_no, 'No') = 'Yes' and name = '%s'" % self.doc.item_code):
			sr_count = sql("select count(name) from `tabSerial No` where item_code = '%s' and warehouse = '%s' and status  ='In Store' and docstatus != 2" % (self.doc.item_code, self.doc.warehouse))[0][0]
			if sr_count != self.doc.actual_qty:
				msg = "Actual Qty(%s) in Bin is mismatched with total number(%s) of serial no in store for item: '%s' and warehouse: '%s'" % (self.doc.actual_qty, sr_count, self.doc.item_code, self.doc.warehouse)
				if getattr(webnotes.defs,'admin_email_notification',1):
					sendmail(['developers@iwebnotes.com'], sender='automail@webnotestech.com', subject='Serial No Count vs Bin Actual Qty', parts=[['text/plain', msg]])			
				msgprint(msg, raise_exception=1)

	# --------------------------------
	# get first stock ledger entry
	# --------------------------------
	
	def get_first_sle(self):
		sle = sql("""
			select * from `tabStock Ledger Entry`
			where item_code = %s
			and warehouse = %s
			and ifnull(is_cancelled, 'No') = 'No'
			order by timestamp(posting_date, posting_time) asc, name asc
			limit 1
		""", (self.doc.item_code, self.doc.warehouse), as_dict=1)
		return sle and sle[0] or None

	def get_prev_sle(self, posting_date = '0000-00-00', posting_time = '00:00'):
		"""get previous stock ledger entry"""
		# get the last sle before the current time-bucket, so that all values
		# are reposted from the current time-bucket onwards.
		# this is necessary because at the time of cancellation, there may be
		# entries between the cancelled entries in the same time-bucket

		sle = sql("""
			select * from `tabStock Ledger Entry`
			where item_code = %s
			and warehouse = %s
			and ifnull(is_cancelled, 'No') = 'No'
			and timestamp(posting_date, posting_time) < timestamp(%s, %s)
			order by timestamp(posting_date, posting_time) desc, name desc
			limit 1
		""", (self.doc.item_code, self.doc.warehouse, posting_date, posting_time), as_dict=1)

		return sle and sle[0] or {}




	# --------------------------------------------------------------------------------------------------------------------------------------
	# validate negative stock (validate if stock is going -ve in between for back dated entries will consider only is_cancel = 'No' entries)
	# --------------------------------------------------------------------------------------------------------------------------------------
	def validate_negative_stock(self, cqty, s):
		diff = cqty + s['actual_qty']
		if  diff < 0 and (abs(diff) > 0.0001) and s['is_cancelled'] != 'Yes':
			msgprint("""
				Negative stock error: 
				Cannot complete this transaction because stock will 
				become negative (%s) for Item <b>%s</b> in Warehouse 
				<b>%s</b> on <b>%s %s</b> in Transaction %s %s""" % \
				(str(diff), self.doc.item_code, self.doc.warehouse,
					s['posting_date'], s['posting_time'], s['voucher_type'], s['voucher_no']), \
					raise_exception=1)

	# ------------------------------------
	# get serialized inventory values
	# ------------------------------------
	def get_serialized_inventory_values(self, val_rate, in_rate, opening_qty, actual_qty, is_cancelled, serial_nos):
		if flt(in_rate) < 0: # wrong incoming rate
			in_rate = val_rate
		elif flt(in_rate) == 0: # In case of delivery/stock issue, get average purchase rate of serial nos of current entry
			in_rate = flt(sql("select ifnull(avg(purchase_rate), 0) from `tabSerial No` where name in (%s)" % (serial_nos))[0][0])

		if in_rate and val_rate == 0: # First entry
			val_rate = in_rate		
		# val_rate is same as previous entry if val_rate is negative
		# Otherwise it will be calculated as per moving average
		elif opening_qty + actual_qty > 0 and ((opening_qty * val_rate) + (actual_qty * in_rate)) > 0:
			val_rate = ((opening_qty *val_rate) + (actual_qty * in_rate)) / (opening_qty + actual_qty)
		stock_val = val_rate
		return val_rate, stock_val



	# ------------------------------------
	# get moving average inventory values
	# ------------------------------------
	def get_moving_average_inventory_values(self, val_rate, in_rate, opening_qty, actual_qty, is_cancelled):
		if flt(in_rate) <= 0: # In case of delivery/stock issue in_rate = 0 or wrong incoming rate
			in_rate = val_rate
		if in_rate and val_rate == 0: # First entry
			val_rate = in_rate

		# val_rate is same as previous entry if :
		# 1. actual qty is negative(delivery note / stock entry)
		# 2. cancelled entry
		# 3. val_rate is negative
		# Otherwise it will be calculated as per moving average
		elif actual_qty > 0 and (opening_qty + actual_qty) > 0 and is_cancelled == 'No' and ((opening_qty * val_rate) + (actual_qty * in_rate)) > 0:
			val_rate = ((opening_qty *val_rate) + (actual_qty * in_rate)) / (opening_qty + actual_qty)
		stock_val = val_rate
		return val_rate, stock_val


	# --------------------------
	# get fifo inventory values
	# --------------------------
	def get_fifo_inventory_values(self, in_rate, actual_qty):
		# add batch to fcfs balance
		if actual_qty > 0:
			self.fcfs_bal.append([flt(actual_qty), flt(in_rate)])

		# remove from fcfs balance
		else:
			withdraw = flt(abs(actual_qty))
			while withdraw:
				if not self.fcfs_bal:
					break # nothing in store
				
				batch = self.fcfs_bal[0]
			 
				if batch[0] <= withdraw:
					# not enough or exactly same qty in current batch, clear batch
					withdraw -= batch[0]
					self.fcfs_bal.pop(0)
				else:
					# all from current batch
					batch[0] -= withdraw
					withdraw = 0

		fcfs_val = sum([flt(d[0])*flt(d[1]) for d in self.fcfs_bal])
		fcfs_qty = sum([flt(d[0]) for d in self.fcfs_bal])
		val_rate = fcfs_qty and fcfs_val / fcfs_qty or 0
		
		return val_rate

	# -------------------
	# get valuation rate
	# -------------------
	def get_valuation_rate(self, val_method, serial_nos, val_rate, in_rate, stock_val, cqty, s):
		if serial_nos:
			val_rate, stock_val = self.get_serialized_inventory_values(val_rate, in_rate, opening_qty = cqty, actual_qty = s['actual_qty'], is_cancelled = s['is_cancelled'], serial_nos = serial_nos)
		elif val_method == 'Moving Average':
			val_rate, stock_val = self.get_moving_average_inventory_values(val_rate, in_rate, opening_qty = cqty, actual_qty = s['actual_qty'], is_cancelled = s['is_cancelled'])
		elif val_method == 'FIFO':
			val_rate = self.get_fifo_inventory_values(in_rate, actual_qty = s['actual_qty'])
		return val_rate, stock_val


	# ----------------
	# get stock value
	# ----------------
	def get_stock_value(self, val_method, cqty, stock_val, serial_nos):
		if val_method == 'Moving Average' or serial_nos:
			stock_val = flt(stock_val) * flt(cqty)
		elif val_method == 'FIFO':
			stock_val = sum([flt(d[0])*flt(d[1]) for d in self.fcfs_bal])
		return stock_val

	def update_entries_after(self, posting_date, posting_time):
		"""update item valution from the give stock ledger entry (sle)
		   onwards."""
		
		# Get prev sle
		prev_sle = self.get_prev_sle(posting_date, posting_time)
		
		# if no prev sle, start from the first one (for repost)
		if not prev_sle:
			cqty, cval, val_rate, self.fcfs_bal = 0, 0, 0, []
		
		# normal
		else:
			cqty = flt(prev_sle.get('bin_aqat', 0))
			cval =flt(prev_sle.get('stock_value', 0))
			val_rate = flt(prev_sle.get('valuation_rate', 0))
			self.fcfs_bal = eval(prev_sle.get('fcfs_stack', '[]') or '[]')

		# get valuation method
		val_method = get_obj('Valuation Control').get_valuation_method(self.doc.item_code)

		# recalculate the balances for all stock ledger entries
		# after the prev sle
		sll = sql("""
			select *
			from `tabStock Ledger Entry` 
			where item_code = %s 
			and warehouse = %s 
			and ifnull(is_cancelled, 'No') = 'No'
			and timestamp(posting_date, posting_time) > timestamp(%s, %s)
			order by timestamp(posting_date, posting_time) asc, name asc""", \
				(self.doc.item_code, self.doc.warehouse, \
					prev_sle.get('posting_date','0000-00-00'), prev_sle.get('posting_time', '00:00')), as_dict = 1)

		for sle in sll:
			# block if stock level goes negative on any date
			self.validate_negative_stock(cqty, sle)

			stock_val, in_rate = 0, sle['incoming_rate'] # IN
			serial_nos = sle["serial_no"] and ("'"+"', '".join(cstr(sle["serial_no"]).split('\n')) \
				+ "'") or ''

			# Get valuation rate
			val_rate, stock_val = self.get_valuation_rate(val_method, serial_nos, \
				val_rate, in_rate, stock_val, cqty, s) 
			
			# Qty upto the sle
			cqty += sle['actual_qty'] 

			# Stock Value upto the sle
			stock_val = self.get_stock_value(val_method, cqty, stock_val, serial_nos) 
			
			# update current sle --> will it be good to update incoming rate in sle 
			# for outgoing stock entry?????
			sql("""update `tabStock Ledger Entry` 
			set bin_aqat=%s, valuation_rate=%s, fcfs_stack=%s, stock_value=%s 
			where name=%s""", (cqty, flt(val_rate), cstr(self.fcfs_bal), stock_val, sle['name']))
		
		# update the bin
		if sll:
			sql("update `tabBin` set valuation_rate=%s, actual_qty=%s, stock_value = %s where name=%s", \
				(flt(val_rate), cqty, flt(stock_val), self.doc.name))


	# item re-order
	# -------------
	def reorder_item(self):
		#check if re-order is required
		projected_qty = flt(self.doc.actual_qty) + flt(self.doc.indented_qty) + flt(self.doc.ordered_qty)
		item_reorder_level = sql("select reorder_level from `%sItem` where name = '%s'" % (self.prefix, self.doc.item_code))[0][0] or 0
		if flt(item_reorder_level) > flt(projected_qty):
			msgprint("Item: " + self.doc.item_code + " is to be re-ordered. Indent raised (Not Implemented).")
	
	# validate
	def validate(self):
		self.validate_mandatory()

	
	# set defaults in bin
	def validate_mandatory(self):
		qf = ['actual_qty', 'reserved_qty', 'ordered_qty', 'indented_qty']
		for f in qf:
			if (not self.doc.fields.has_key(f)) or (not self.doc.fields[f]): 
				self.doc.fields[f] = 0.0
