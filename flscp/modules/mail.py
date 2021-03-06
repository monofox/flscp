#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# vim: fenc=utf-8:ts=8:sw=8:si:sta:noet
import os
import os.path
import subprocess
import shlex
import hashlib
import random
import datetime
import logging
import copy
import zlib
import uuid
import re
from database import MailDatabase, SaslDatabase
from flsconfig import FLSConfig
from modules.domain import Domain
from pwgen import generate_pass
from saltencryption import SaltEncryption
from mailer import Mailer
from tools import hashPostFile

def MailValidator(email):
	if email is None:
		return False

	return re.match(r"^[a-zA-Z0-9._%\-+]+\@[a-zA-Z0-9._%\-]+\.[a-zA-Z]{2,}$", email) is not None

class MailAccountList:

	def __init__(self):
		self._items = []

	def add(self, item):
		self._items.append(item)

	def remove(self, obj):
		self._items.remove(obj)

	def __getitem__(self, key):
		return self._items[key]

	def __setitem__(self, key, value):
		self._items[key] = value

	def __delitem__(self, key):
		del(self._items[key])

	def __iter__(self):
		for f in self._items:
			yield f

	def __contains__(self, item):
		return True if item in self._items else False

	def __len__(self):
		return len(self._items)

	def findById(self, itemId):
		item = None
		try:
			id = int(itemId)
		except:
			pass

		for f in self._items:
			if f.id == itemId:
				item = f
				break

		return item

	def findByDomain(self, domain):
		for f in self._items:
			if f.domain == domain:
				return True

		return False

class MailAccount:
	TYPE_ACCOUNT = 'account'
	TYPE_FORWARD = 'forward'
	TYPE_FWDSMTP = 'fwdsmtp'

	STATE_OK = 'ok'
	STATE_CHANGE = 'change'
	STATE_CREATE = 'create'
	STATE_DELETE = 'delete'
	STATE_QUOTA = 'quota'

	def __init__(self):
		conf = FLSConfig.getInstance()
		self.id = None
		self.type = MailAccount.TYPE_ACCOUNT
		self.state = MailAccount.STATE_OK
		if conf is not None and conf.has_option('userdefault', 'quota'):
			self.quota = conf.getint('userdefault', 'quota')
		else:
			# By default disabled
			self.quota = 0
		self.quotaSts = 0.0
		self.mail = ''
		self.domain = ''
		self.pw = ''
		self.hashPw = ''
		self.genPw = False
		self.altMail = ''
		self.alias = False
		self.forward = []
		self.authCode = None
		self.authValid = None
		self.encryption = False
		self.privateKey = ''
		self.publicKey = ''
		self.privateKeySalt = ''
		self.privateKeyIterations = 10
		self.filterPostgrey = False
		self.filterVirus = False
		self.filterSpam = False
		self.enabled = True

	def getMailAddress(self):
		return '%s@%s' % (self.mail, self.domain)

	def generateId(self):
		self.id = 'Z%s' % (str(zlib.crc32(uuid.uuid4().hex.encode('utf-8')))[0:3],)

	def createAuthCode(self):
		self.authCode = hashlib.md5(str(hash(random.SystemRandom().uniform(0, 1000))).encode('utf-8')).hexdigest()
		self.authValid = datetime.datetime.now() + datetime.timedelta(hours=2)

		db = MailDatabase.getInstance()
		try:
			cx = db.getCursor()
			query = (
				'UPDATE mail_users SET authcode = %s, authvalid = %s WHERE mail_id = %s'
			)
			cx.execute(query, (self.authCode, self.authValid.strftime('%Y-%m-%d %H:%M:%S'), self.id))
			db.commit()
			cx.close()
		except:
			return False
		else:
			return True

	def getHomeDir(self):
		conf = FLSConfig.getInstance()
		return os.path.join(conf.get('mailserver', 'basemailpath'), 'virtual', self.domain, self.mail)

	def getMailDir(self):
		return os.path.join(self.getHomeDir(), 'mails')

	def getMailDirFormat(self):
		return 'maildir:' + os.path.join('~', 'mails')

	def authenticate(self, mech, pwd, cert = None):
		conf = FLSConfig.getInstance()
		log = logging.getLogger('flscp')
		data = {
			'userdb_user': '',
			'userdb_home': '',
			'userdb_uid': '',
			'userdb_gid': '',
			'userdb_mail': '',
			'userdb_quota_rule': '',
			'userdb_scrambler_enabled': 0,
			'nopassword': 1
		}
		localPartDir = os.path.join(conf.get('mailserver', 'basemailpath'), 'virtual')
		username = ('%s@%s' % (self.mail, self.domain)).lower()
		if self.hashPw == '_no_':
			log.debug('User %s can not login, because password is disabled!' % (self.getMailAddress(),))
			return False

		s = SaltEncryption()

		if mech in ['PLAIN', 'LOGIN']:
			state = s.compare(pwd, self.hashPw)
		elif mech in ['EXTERNAL']:
			state = (cert.lower() == 'valid' and pwd == '')
		else:
			log.debug('User %s can not login: unsupported auth mechanism "%s"' % (self.getMailAddress(), mech))
			state = False

		if state:
			data['userdb_user'] = username
			data['userdb_home'] = self.getHomeDir()
			data['userdb_uid'] = conf.get('mailserver', 'uid')
			data['userdb_gid'] = conf.get('mailserver', 'gid')
			data['userdb_mail'] = self.getMailDirFormat()
			data['userdb_quota_rule'] = '*:storage=%sb' % (self.quota,)
			data['userdb_scrambler_enabled'] = '1' if self.encryption else '0'
			if self.encryption:
				data['userdb_scrambler_plain_password'] = pwd if self.encryption else ''
				data['userdb_scrambler_public_key'] = self.publicKey.replace('\n', '_')
				data['userdb_scrambler_private_key'] = self.privateKey.replace('\n', '_')
				data['userdb_scrambler_private_key_salt'] = self.privateKeySalt[self.privateKeySalt.rindex('$') + 1:]
				data['userdb_scrambler_private_key_iterations'] = self.privateKeyIterations

			return data

		else:
			return False

	def validatePassword(self, currentPassword):
		s = SaltEncryption()
		return s.compare(currentPassword, self.hashPw)

	def getUserLookup(self):
		"""
		Returns the dictionary for the ...
		"""
		conf = FLSConfig.getInstance()
		data = {
			'home': self.getHomeDir(),
			'uid': conf.get('mailserver', 'uid'),
			'gid': conf.get('mailserver', 'gid'),
			'quota_rule': '*:storage=%sb' % (self.quota,),
			'scrambler_enabled': '1' if self.encryption else '0'
		}

		if self.encryption:
				data['scrambler_public_key'] = self.publicKey.replace('\n', '_')
				data['scrambler_private_key'] = self.privateKey.replace('\n', '_')
				data['scrambler_private_key_salt'] = self.privateKeySalt[self.privateKeySalt.rindex('$') + 1:]
				data['scrambler_private_key_iterations'] = self.privateKeyIterations

		return data

	def markQuotaCalc(self):
		if self.state == MailAccount.STATE_OK:
			self.state = MailAccount.STATE_QUOTA

	def toggleStatus(self):
		if self.enabled:
			self.enabled = False
		else:
			self.enabled = True

		self.state = MailAccount.STATE_CHANGE

	def changePassword(self, currentPassword, newPassword):
		"""
		This method changes the password of an user.
		This method cannot be called from client side, only from CP Server!

		@currentPassword: contains the current password. Necessary in order to change encryption.
		@newPassword: the new password. 
		"""
		self.pw = newPassword
		self.hashPassword()
		self.updatePrivateKey(currentPassword, newPassword)
		db = MailDatabase.getInstance()
		try:
			cx = db.getCursor()
			query = (
				'UPDATE mail_users SET mail_pass = %s, authcode = NULL, authvalid = NULL ' \
				'private_key = %s, private_key_salt = %s, private_key_iterations = %s ' \
				'WHERE mail_id = %s'
			)
			cx.execute(query, 
				(
					self.hashPw, self.privateKey, self.privateKeySalt, 
					str(int(self.privateKeyIterations)), self.id
				)
			)
			db.commit()
			cx.close()
		except:
			return False
		else:
			self.updateCredentials()
			return True

	def hashPassword(self):
		s = SaltEncryption()
		# idea for later: store hash with:
		# s.hash(md5(self.pw)) and check it later with s.compare(md5(self.pw), <hash>)
		# or do it with sha512
		self.hashPw = s.hash(self.pw)

	# this is not allowed on client side! Only here....
	def generatePassword(self):
		log = logging.getLogger('flscp')
		log.info('Generating password for user %s' % (self.mail,))
		self.pw = generate_pass(12)

	def getQuota(self):
		return self.quota

	def getQuotaMb(self):
		try:
			return round(self.quota/1024/1024, 0)
		except:
			return 1

	def getQuotaReadable(self):
		try:
			quotaKb = round(self.quota/1024, 0)
		except TypeError:
			return ''
		quotaMb = round(self.quota/1024/1024, 0)
		quotaGb = round(self.quota/1024/1024/1024, 0)
		if quotaGb < 1:
			if quotaMb < 1:
				if quotaKb < 1:
					return str(self.quota) + ' B'
				else:
					return str(quotaKb) + ' KB'
			else:
				return str(quotaMb) + ' MB'
		else:
			return str(quotaGb) + ' GB'
	
	def getQuotaStatus(self):
		if self.quotaSts is not None:
			return str(self.quotaSts) + ' %'
		else:
			return str(0.00) + ' %'

	def generateEncryptionSalt(self):
		"""
		This method generates the necessary blowfish salt for hashing
		the password. This is necessary for the scrambler plugin of posteo.

		This method needs the blank password set.
		"""
		import bcrypt
		# we need a plain text password!
		if len(self.pw) <= 0:
			return

		self.privateKeySalt = bcrypt.gensalt(self.privateKeyIterations, b'2a')

		return self.privateKeySalt

	def generateCertificates(self):
		"""
		This method generates a certification pair.
		Based on the salt generated by `generateEncryptionSalt`, the private
		key is encrypted with the hashed password.
		"""
		import bcrypt, OpenSSL
		# if there is no salt generated yet, do it now.
		if len(self.privateKeySalt) <= 0:
			self.generateEncryptionSalt()
		# first we need the hashed password. 
		if len(self.pw) <= 0 or len(self.privateKeySalt) <= 0:
			return
		hashedPw = bcrypt.hashpw(self.pw.encode('utf-8'), self.privateKeySalt)
		pkey = OpenSSL.crypto.PKey()
		pkey.generate_key(OpenSSL.crypto.TYPE_RSA, 2048)
		if not pkey.check():
			raise Exception('Could not generate private key!')

		self.privateKey = OpenSSL.crypto.dump_privatekey(OpenSSL.crypto.FILETYPE_PEM, pkey, 'blowfish', hashedPw)
		self.publicKey = OpenSSL.crypto.dump_publickey(OpenSSL.crypto.FILETYPE_PEM, pkey)

	def resetEncryption(self):
		"""
		Resets the mail encryption for scrambler plugin of posteo.
		"""
		self.encryption = False
		self.hashedPw = ''
		self.publicKey = ''
		self.privateKey = ''
		self.privateKeySalt = ''

	def encryptMails(self):
		"""
		This method encrypts all mails after enabling the scrambler plugin of posteo.
		FIXME: write me....
		"""
		pass

	def updatePrivateKey(self, oldPassword, newPassword):
		if len(self.privateKey) <= 0 or len(self.privateKeySalt) <= 0:
			return None

		import bcrypt, OpenSSL
		hashedPw = bcrypt.hashpw(oldPassword.encode('utf-8'), self.privateKeySalt)
		try:
			pkey = OpenSSL.crypto.load_privatekey(OpenSSL.crypto.FILETYPE_PEM, self.privateKey, oldPassword)
		except:
			return None

		self.generateEncryptionSalt()
		hashedPw = bcrypt.hashpw(newPassword.encode('utf-8'), self.privateKeySalt)
		self.privateKey = OpenSSL.crypto.dump_privatekey(OpenSSL.crypto.FILETYPE_PEM, pkey, 'blowfish', hashedPw)

	def save(self):
		"""
		Saves a mail account.
		"""
		log = logging.getLogger('flscp')
		conf = FLSConfig.getInstance()

		if self.state == MailAccount.STATE_CREATE:
			self.create()
			return
		elif self.state == MailAccount.STATE_DELETE:
			self.delete()
			return
		elif self.state == MailAccount.STATE_QUOTA:
			self.recalculateQuota()
			return

		# now save!
		# -> see create - but if key changed (mail address!) remove
		# all entries before and rename folder in /var/mail,... directory
		# get original data!
		if not self.exists():
			self.create()

		# get domain id! (if not exist: create!)
		try:
			d = Domain.getByName(self.domain)
		except KeyError:
			raise

		# pw entered?
		if len(self.pw.strip()) > 0:
			log.info('Hash password for user %s' % (self.mail,))
			self.hashPassword()

		db = MailDatabase.getInstance()
		cx = db.getCursor()
		query = ('SELECT mail_id, mail_addr, mail_type, encryption FROM mail_users WHERE mail_id = %s')
		cx.execute(query, (self.id,))
		(mail_id, mail_addr, mail_type, encryption) = cx.fetchone()
		(mail, domain) = mail_addr.split('@')
		encryption = bool(encryption)
		cx.close()

		# if the encryption setting changed, update certificates.
		if encryption != self.encryption:
			if self.encryption:
				if len(self.pw.strip()) <= 0:
					# we cannot create certificates without password...
					self.resetEncryption()
				else:
					self.generateCertificates()
			else:
				self.resetEncryption()

		cx = db.getCursor()
		if (self.type == MailAccount.TYPE_ACCOUNT and self.hashPw != '') \
			or (self.type == MailAccount.TYPE_FWDSMTP and self.hashPw != '') \
			or self.type == MailAccount.TYPE_FORWARD:
			query = (
				'UPDATE mail_users SET mail_acc = %s, mail_pass = %s, mail_forward = %s, ' \
				'domain_id = %s, mail_type = %s, status = %s, quota = %s, mail_addr = %s, ' \
				'alternative_addr = %s, alias = %s, encryption = %s, public_key = %s, ' \
				'private_key = %s, private_key_salt = %s, private_key_iterations = %s, '\
				'filter_postgrey = %s, filter_spam = %s, filter_virus = %s, enabled = %s '\
				'WHERE mail_id = %s'
			)
			params = (
				self.mail, self.hashPw, ','.join(self.forward), d.id, self.type, self.state, self.quota, 
				'%s@%s' % (self.mail, self.domain), self.altMail, str(int(self.alias)), 
				str(int(self.encryption)), self.publicKey, self.privateKey, self.privateKeySalt, 
				self.privateKeyIterations, str(int(self.filterPostgrey)), str(int(self.filterSpam)), 
				str(int(self.filterVirus)), str(int(self.enabled)), self.id
			)
		else:
			query = (
				'UPDATE mail_users SET mail_acc = %s, mail_forward = %s, ' \
				'domain_id = %s, mail_type = %s, status = %s, quota = %s, mail_addr = %s, ' \
				'alternative_addr = %s, alias = %s, encryption = %s, public_key = %s, ' \
				'private_key = %s, private_key_salt = %s, private_key_iterations = %s, ' \
				'filter_postgrey = %s, filter_spam = %s, filter_virus = %s, enabled = %s ' \
				'WHERE mail_id = %s'
			)
			params = (
				self.mail, ','.join(self.forward), d.id, self.type, self.state, self.quota, 
				'%s@%s' % (self.mail, self.domain), self.altMail, str(int(self.alias)), 
				str(int(self.encryption)), self.publicKey, self.privateKey, self.privateKeySalt, 
				self.privateKeyIterations, str(int(self.filterPostgrey)), str(int(self.filterSpam)), 
				str(int(self.filterVirus)), str(int(self.enabled)), self.id
			)

		cx.execute(
			query, 
			params
		)
		db.commit()
		log.debug('executed mysql statement: %s' % (cx.statement,))

		# update credentials...
		# if pw was entered or type changed
		if mail_type != self.type or self.pw.strip() != '':
			self.updateCredentials()
			# do we need to encrypt?
			if encryption != self.encryption:
				self.encryptMails()

		# now update mailboxes files!
		if not self.updateMailboxes(oldMail=mail, oldDomain=domain):
			cx.close()
			return False

		# update aliases
		if not self.updateAliases(oldMail=mail, oldDomain=domain):
			# remove entry from updateMailboxes?
			cx.close()
			return False

		# update sender-access
		if not self.updateSenderAccess(oldMail=mail, oldDomain=domain):
			# remove entry from updateMailboxes and Aliases ?
			cx.close()
			return False

		# update login maps
		if not self.updateLoginMaps(oldMail=mail, oldDomain=domain):
			# remove entry from updateMailboxes and Aliases ?
			cx.close()
			return False

		# update postgrey whitelist 
		if not self.updatePostgrey(oldMail=mail, oldDomain=domain):
			# remove entry from updateMailboxes and Aliases ?
			cx.close()
			return False

		# update amavis filter files 
		if not self.updateAmavis(oldMail=mail, oldDomain=domain):
			# remove entry from updateMailboxes and Aliases ?
			cx.close()
			return False

		# rename folders - but only if target directory does not exist
		# (we had to throw fatal error if target directory exists!)
		oldPath = '%s/%s/%s/' % (conf.get('mailserver', 'basemailpath'), domain, mail)
		path = '%s/%s/%s/' % (conf.get('mailserver', 'basemailpath'), self.domain, self.mail)
		if os.path.exists(oldPath):
			if os.path.exists(path):
				log.error('Could not move "%s" to "%s", because it already exists!' % (oldPath, path))
			else:
				try:
					os.rename(oldPath, path)
				except OSError as e:
					log.warning('Got OSError - Does directory exists? (%s)' % (e,))
				except Exception as e:
					log.warning('Got unexpected exception (%s)!' % (e,))

		cx.close()

		# all best? Than go forward and update set state,...
		self.setState(MailAccount.STATE_OK)

		# notify
		if len(self.altMail) > 0:
			m = Mailer(self)
			state = False
			if self.type == MailAccount.TYPE_ACCOUNT \
					or self.type == MailAccount.TYPE_FWDSMTP:
				state = m.changeAccount()
			else:
				state = m.changeForward()

			if state:
				log.info('User is notified about account change!')
			else:
				log.warning('Unknown error while notifying user!')
		else:
			log.info('User is not notified because we have no address of him!')

		# reset info
		self.pw = ''
		self.hashPw = ''
		self.genPw = False

	def delete(self):
		log = logging.getLogger('flscp')
		conf = FLSConfig.getInstance()

		# delete!
		# 1. remove credentials
		# 2. remove entry from /etc/postfix/fls/aliases
		# 3. remove entry from /etc/postfix/fls/mailboxes
		# 4. remove entry from /etc/postfix/fls/sender-access
		# 5. remove entry from mail_users
		# 7. remove complete mails in /var/mail/,... directory
		# 6. postmap all relevant entries
		self.updateCredentials()
		self.updateMailboxes()
		self.updateAliases()
		self.updateSenderAccess()
		self.updateLoginMaps()
		self.updatePostgrey()
		self.updateAmavis()

		if self.exists():
			db = MailDatabase.getInstance()
			cx = db.getCursor()
			query = ('SELECT mail_id, mail_addr FROM mail_users WHERE mail_id = %s')
			cx.execute(query, (self.id,))
			for (mail_id, mail_addr,) in cx:
				(mail, domain) = mail_addr.split('@')
				path = '%s/%s/%s/' % (conf.get('mailserver', 'basemailpath'), domain, mail) 
				if os.path.exists(path):
					try:
						os.removedirs(path)
					except Exception as e:
						log.warning('Error when removing directory: %s' % (e,))

			query = ('DELETE FROM mail_users WHERE mail_id = %s')
			cx.execute(query, (self.id,))
			cx.close()

	def recalculateQuota(self):
		log = logging.getLogger('flscp')
		conf = FLSConfig.getInstance()

		cmd = shlex.split('%s quota recalc -u %s' % (conf.get('mailserver', 'doveadm'), '%s@%s' % (self.mail, self.domain)))
		state = True
		with subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as p:
			out = p.stdout.read()
			err = p.stderr.read()
			if len(out) > 0:
				log.info(out)
			if len(err) > 0:
				log.warning(err)
				state = False

		self.setState(MailAccount.STATE_OK)
		return state

	def exists(self):
		# check if entry exists already in mail_users!
		db = MailDatabase.getInstance()
		cx = db.getCursor()
		query = ('SELECT mail_id FROM mail_users WHERE mail_addr = %s')
		cx.execute(query, ('%s@%s' % (self.mail, self.domain),))
		exists = len(cx.fetchall()) > 0
		cx.close()
		return exists

	def create(self):
		log = logging.getLogger('flscp')
		# create:
		# 1. update mail_users
		# 2. update credentials, if given
		# 3. update /etc/postfix/fls/mailboxes
		# 4. update aliases
		# 5. update sender-access (we could later be implement to restrict sending!)
		# postmap all relevant entries
		if self.exists():
			# already exists! 
			raise KeyError('Mail "%s@%s" already exists!' % (self.mail, self.domain))
		
		# get domain id! (if not exist: create!)
		try:
			d = Domain.getByName(self.domain)
		except KeyError:
			raise

		# pw entered?
		if len(self.pw.strip()) > 0:
			self.hashPassword()

		# if the encryption setting changed, update certificates.
		if self.encryption:
			if len(self.pw.strip()) <= 0:
				# we cannot create certificates without password...
				self.resetEncryption()
			else:
				self.generateCertificates()

		db = MailDatabase.getInstance()
		cx = db.getCursor()
		query = (
			'INSERT INTO mail_users (mail_acc, mail_pass, mail_forward, domain_id, mail_type, ' \
			'status, quota, mail_addr, alternative_addr, alias, encryption, public_key, private_key, ' \
			'private_key_salt, private_key_iterations, filter_postgrey, filter_spam, filter_virus, enabled) ' \
			'VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)'
		)
		cx.execute(
			query, 
			(
				self.mail, self.hashPw, ','.join(self.forward), d.id, self.type, self.state, self.quota, 
				'%s@%s' % (self.mail, self.domain), self.altMail, str(int(self.alias)), 
				str(int(self.encryption)), self.publicKey, self.privateKey, self.privateKeySalt, 
				self.privateKeyIterations, str(int(self.filterPostgrey)), str(int(self.filterSpam)), 
				str(int(self.filterVirus)), str(int(self.enabled))
			)
		)
		db.commit()
		log.debug('executed mysql statement: %s' % (cx.statement,))
		mailId = cx.lastrowid
		if mailId is None:
			cx.close()
			return False
		else:
			self.id = mailId

		# update credentials... (we don't need to encrypt.. there are no mails ;))
		self.updateCredentials()

		# now update mailboxes files!
		if not self.updateMailboxes():
			cx.close()
			return False

		# update aliases
		if not self.updateAliases():
			# remove entry from updateMailboxes?
			cx.close()
			return False

		# update sender-access
		if not self.updateSenderAccess():
			# remove entry from updateMailboxes and Aliases ?
			cx.close()
			return False

		# update the login maps
		if not self.updateLoginMaps():
			# remove entry from updateMailboxes and Aliases ?
			cx.close()
			return False

		# update postgrey whitelist
		if not self.updatePostgrey():
			# remove entry from updateMailboxes and Aliases ?
			cx.close()
			return False

		# update amavis filter
		if not self.updateAmavis():
			# remove entry from updateMailboxes and Aliases ?
			cx.close()
			return False

		cx.close()

		# all best? Than go forward and update set state,...
		self.setState(MailAccount.STATE_OK)

		# notify 
		if len(self.altMail) > 0:
			m = Mailer(self)
			state = False
			if self.type == MailAccount.TYPE_ACCOUNT \
					or self.type == MailAccount.TYPE_FWDSMTP:
				state = m.newAccount()
			else:
				state = m.newForward()

			if state:
				log.info('User is notified about account change!')
			else:
				log.warning('Unknown error while notifying user!')
		else:
			log.info('User is not notified because we have no address of him!')

		# reset info
		self.pw = ''
		self.hashPw = ''
		self.genPw = False

	def setState(self, state):
		db = MailDatabase.getInstance()
		cx = db.getCursor()
		query = ('UPDATE mail_users SET status = %s WHERE mail_id = %s')
		cx.execute(query, (state, self.id))
		db.commit()
		cx.close()

		self.state = state

	def updateMailboxes(self, oldMail = None, oldDomain = None):
		conf = FLSConfig.getInstance()

		mailAddr = '%s@%s' % (self.mail, self.domain)
		if oldMail is None:
			oldMail = self.mail
		if oldDomain is None:
			oldDomain = self.domain
		mailOldAddr = '%s@%s' % (oldMail, oldDomain)

		cnt = []
		with open(conf.get('mailserver', 'mailboxes'), 'r') as f:
			cnt = f.read().split('\n')

		cnt = [f for f in cnt if (('\t' in f and f[0:f.index('\t')] != mailOldAddr) or f[0:1] == '#') and len(f.strip()) > 0]

		# now add data:
		if self.state in (MailAccount.STATE_CHANGE, MailAccount.STATE_CREATE):
			if self.type == MailAccount.TYPE_ACCOUNT:
				cnt.append('%s\t%s%s%s%s' % (mailAddr, self.domain, os.sep, self.mail, os.sep))

		# now sort file
		cnt.sort()

		# now write back
		try:
			with open(conf.get('mailserver', 'mailboxes'), 'w') as f:
				f.write('\n'.join(cnt))
		except:
			return False
		else:
			# postmap
			return hashPostFile(conf.get('mailserver', 'mailboxes'), conf.get('mailserver', 'postmap'))

	def updateAliases(self, oldMail = None, oldDomain = None):
		conf = FLSConfig.getInstance()

		mailAddr = '%s@%s' % (self.mail, self.domain)
		if oldMail is None:
			oldMail = self.mail
		if oldDomain is None:
			oldDomain = self.domain
		mailOldAddr = '%s@%s' % (oldMail, oldDomain)

		cnt = []
		with open(conf.get('mailserver', 'aliases'), 'r') as f:
			cnt = f.read().split('\n')

		cnt = [f for f in cnt if (('\t' in f and f[0:f.index('\t')] != mailOldAddr) or f[0:1] == '#') and len(f.strip()) > 0]

		# now add data:
		if self.state in (MailAccount.STATE_CHANGE, MailAccount.STATE_CREATE):
			forward = copy.copy(self.forward)
			# remove all empty things
			i = 0
			for f in forward:
				if len(f.strip()) <= 0:
					del(forward[i])

				i += 1
				
			if self.type == MailAccount.TYPE_ACCOUNT:
				forward.insert(0, mailAddr)
			forward = list(set(forward))
			cnt.append('%s\t%s' % (mailAddr, ','.join(forward)))

		# now sort file
		cnt.sort()

		# now write back
		try:
			with open(conf.get('mailserver', 'aliases'), 'w') as f:
				f.write('\n'.join(cnt))
		except:
			return False
		else:
			# postmap
			return hashPostFile(conf.get('mailserver', 'aliases'), conf.get('mailserver', 'postmap'))

	def updateSenderAccess(self, oldMail = None, oldDomain = None):
		conf = FLSConfig.getInstance()
		
		mailAddr = '%s@%s' % (self.mail, self.domain)
		if oldMail is None:
			oldMail = self.mail
		if oldDomain is None:
			oldDomain = self.domain
		mailOldAddr = '%s@%s' % (oldMail, oldDomain)

		cnt = []
		with open(conf.get('mailserver', 'senderaccess'), 'r') as f:
			cnt = f.read().split('\n')

		cnt = [f for f in cnt if (('\t' in f and f[0:f.index('\t')] != mailOldAddr) or f[0:1] == '#') and len(f.strip()) > 0]

		# now add data:
		if self.state in (MailAccount.STATE_CHANGE, MailAccount.STATE_CREATE):
			if self.enabled:
				cnt.append('%s\t%s' % (mailAddr, 'OK'))
			else:
				cnt.append('%s\t%s' % (mailAddr, 'REJECT'))

		# now sort file
		cnt.sort()

		# now write back
		try:
			with open(conf.get('mailserver', 'senderaccess'), 'w') as f:
				f.write('\n'.join(cnt))
		except:
			return False
		else:
			# postmap
			return hashPostFile(conf.get('mailserver', 'senderaccess'), conf.get('mailserver', 'postmap'))

	def updateLoginMaps(self, oldMail = None, oldDomain = None):
		conf = FLSConfig.getInstance()
		db = MailDatabase.getInstance()
		log = logging.getLogger('flscp')
		cnt = []

		# first retrieve all normal accounts!
		cx = db.getCursor()
		query = ('SELECT mail_addr FROM mail_users WHERE enabled = 1 and alias = 0')
		cx.execute(query)
		try:
			for (mail_addr, ) in cx:
				cnt.append('%s\t%s' % (mail_addr, mail_addr))
		except:
			log.error('Reading database failed in MailAccount::updateLoginMaps.')

		# now retrieve all aliases
		query = ('SELECT mail_addr, alternative_addr FROM mail_users WHERE enabled = 1 and alias = 1')
		cx.execute(query)
		try:
			for (mail_addr, alternative_addr) in cx:
				cnt.append('%s\t%s' % (mail_addr, alternative_addr))
		except:
			log.error('Reading database failed in MailAccount::updateLoginMaps.')

		cnt.append('')

		cx.close()

		# now write back
		try:
			with open(conf.get('mailserver', 'sendermaps'), 'w') as f:
				f.write('\n'.join(cnt))
		except:
			return False
		else:
			# postmap
			return hashPostFile(conf.get('mailserver', 'sendermaps'), conf.get('mailserver', 'postmap'))

	def updatePostgrey(self, oldMail = None, oldDomain = None):
		conf = FLSConfig.getInstance()
		db = MailDatabase.getInstance()
		log = logging.getLogger('flscp')
		cx = db.getCursor()

		fname = conf.get('mailserver', 'postgrey_whitelist')
		cnt = []
		cnt.append('# postgrey whitelist for mail recipients')
		cnt.append('# --------------------------------------')
		cnt.append('# This fils is auto generated by FLS CP')
		cnt.append('# DO NOT EDIT THIS FILE MANUALLY!')
		cnt.append('')

		query = ('SELECT mail_addr FROM mail_users WHERE filter_postgrey = 0 and enabled = 1')
		cx.execute(query)
		try:
			for (mail_addr,) in cx:
				cnt.append(mail_addr)
		except:
			log.error('Reading database failed in MailAccount::updatePostgrey.')
		cx.close()

		# now save the postgrey file.
		try:
			with open(fname, 'w') as f:
				f.write('\n'.join(cnt))
		except:
			log.error('Could not save recipient whitelist for postgrey in %s.' % (fname,))
			return False

		return True

	def updateAmavis(self, oldMail = None, oldDomain = None):
		"""
		Example:
		@spam_lovers_maps = @bypass_spam_checks_maps = (
			[ qw( user1@... user2@... ) ],
		);
		"""
		log = logging.getLogger('flscp')
		conf = FLSConfig.getInstance()
		fname = conf.get('mailserver', 'amavis_whitelist')
		db = MailDatabase.getInstance()
		cx = db.getCursor()

		cnt = []
		cnt.append('use strict;')
		cnt.append('# Amavis whitelist for mail recipients')
		cnt.append('# --------------------------------------')
		cnt.append('# This fils is auto generated by FLS CP')
		cnt.append('# DO NOT EDIT THIS FILE MANUALLY!')
		cnt.append('')
		# first create a list of exceptions for spam.
		cnt.append('@spam_lovers_maps = @bypass_spam_checks_maps = (')
		query = ('SELECT mail_addr FROM mail_users WHERE filter_spam = 0 and enabled = 1')
		cx.execute(query)
		try:
			for (mail_addr,) in cx:
				cnt.append(mail_addr)
		except:
			log.error('Reading database for antispam failed in MailAccount::updatePostgrey.')
		cnt.append(');')
		cnt.append('')

		# second: a list with users who don't want virus check.
		cnt.append('@virus_lovers_maps = @bypass_virus_checks_maps = (')
		query = ('SELECT mail_addr FROM mail_users WHERE filter_virus = 0 and enabled = 1')
		cx.execute(query)
		try:
			for (mail_addr,) in cx:
				cnt.append(mail_addr)
		except:
			log.error('Reading database for antivirus failed in MailAccount::updatePostgrey.')
		cnt.append(');')

		cnt.append('')
		cnt.append('1;	# ensure a defined return')
		cx.close()

		# now save the postgrey file.
		try:
			with open(fname, 'w') as f:
				f.write('\n'.join(cnt))
		except:
			log.error('Could not save whitelist for amavis in %s.' % (fname,))
			return False

		return True

	def credentialsKey(self):
		return '%s\x00%s\x00%s' % (self.mail, self.domain, 'userPassword')

	def updateCredentials(self):
		conf = FLSConfig.getInstance()
		if not conf.getboolean('features', 'sasldb'):
			return None

		db = SaslDatabase.getInstance()

		if self.state == MailAccount.STATE_DELETE or len(self.hashPw.strip()) <= 0:
			db.delete(self.credentialsKey())
		else:
			if db.exists(self.credentialsKey()):
				db.update(self.credentialsKey(), self.pw)
			else:
				db.add(self.credentialsKey(), self.pw)

	@classmethod
	def getByEMail(self, mail):
		log = logging.getLogger('flscp')
		ma = MailAccount()
		db = MailDatabase.getInstance()
		cx = db.getCursor()
		query = (
			'SELECT mail_id, mail_acc, mail_pass, mail_forward, domain_id, mail_type, sub_id, status, ' \
			'filter_postgrey, filter_virus, filter_spam, quota, mail_addr, alternative_addr, alias, authcode, ' \
			'authvalid, encryption, public_key, private_key, private_key_salt, private_key_iterations, ' \
			'enabled FROM mail_users WHERE mail_addr = %s'
		)
		cx.execute(query, (mail.lower(),))
		if cx is None:
			log.warning('Execution failed in MailAccount::getByEMail(%s).' % (mail,))
			return None

		try:
			resultRow = cx.fetchone()
		except Exception as e:
			log.critical('Got error in MailAccount::getByEMail: %s' % (e,))
			try:
				cx.close()
			except:
				pass
			return None
		else:
			if resultRow is None:
				log.info('No user found by mail %s' % (mail,))
				return None

		try:
			(
				mail_id, mail_acc, mail_pass, mail_forward, domain_id, mail_type, sub_id, status, 
				filter_postgrey, filter_virus, filter_spam, quota, mail_addr, alternative_addr, 
				alias, authcode, authvalid, encryption, public_key, private_key, private_key_salt, 
				private_key_iterations, enabled
			) = resultRow
			ma.id = mail_id
			ma.quota = quota
			ma.mail = mail_acc
			ma.hashPw = mail_pass
			ma.domain = mail_addr.split('@')[1]
			ma.altMail = alternative_addr
			ma.alias = alias
			ma.forward = mail_forward.split(',')
			ma.type = MailAccount.TYPE_ACCOUNT
			if mail_type == 'fwdsmtp':
				ma.type = MailAccount.TYPE_FWDSMTP
			elif mail_type == 'forward':
				ma.type = MailAccount.TYPE_FORWARD
			ma.status = status
			ma.authCode = authcode
			ma.authValid = authvalid
			ma.encryption = bool(encryption)
			ma.publicKey = public_key
			ma.privateKey = private_key
			ma.privateKeyIterations = private_key_iterations
			ma.privateKeySalt = private_key_salt
			ma.filterPostgrey = filter_postgrey
			ma.filterSpam = filter_spam
			ma.filterVirus = filter_virus
			ma.enabled = bool(enabled)
		except Exception as e:
			log.critical('Got error in MailAccount::getByEMail: %s' % (e,))
			cx.close()
			return None
		else:
			cx.close()
			self = ma
			return self

	def __eq__(self, obj):
		log = logging.getLogger('flscp')
		log.debug('Compare objects!!!')
		if obj is None or self is None:
			return False

		if self.id == obj.id and \
			self.type == obj.type and \
			self.mail.lower() == obj.mail.lower() and \
			self.domain == obj.domain and \
			self.pw == obj.pw and \
			self.genPw == obj.genPw and \
			self.altMail == obj.altMail and \
			self.alias == obj.alias and \
			self.forward == obj.forward and \
			self.state == obj.state and \
			self.enabled == obj.enabled and \
			self.quota == obj.quota and \
			self.encryption == obj.encryption and \
			self.publicKey == obj.publicKey and \
			self.privateKey == obj.privateKey and \
			self.privateKeySalt == obj.privateKeySalt and \
			self.privateKeyIterations == obj.privateKeyIterations and \
			self.filterPostgrey == obj.filterPostgrey and \
			self.filterSpam == obj.filterSpam and \
			self.filterVirus == obj.filterVirus:
			return True
		else:
			return False

	def __ne__(self, obj):
		return not self.__eq__(obj)

	@classmethod
	def fromDict(ma, data):
		self = ma()

		self.id = data['id']
		self.type = data['type']
		self.mail = data['mail'].lower()
		self.domain = data['domain'].lower()
		self.altMail = data['altMail']
		self.alias = data['alias']
		self.forward = data['forward']
		self.state = data['state']
		self.pw = data['pw']
		self.genPw = data['genPw']
		self.enabled = data['enabled']
		self.encryption = data['encryption']
		self.publicKey = data['publicKey']
		self.privateKey = data['privateKey']
		self.privateKeyIterations = data['privateKeyIterations']
		self.privateKeySalt = data['privateKeySalt']
		self.filterPostgrey = data['filterPostgrey']
		self.filterSpam = data['filterSpam']
		self.filterVirus = data['filterVirus']
		self.quota = data['quota']
		if 'quotaSts' in data:
			self.quotaSts = data['quotaSts']

		return self
