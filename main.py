import os
import webapp2
from google.appengine.ext import ndb
from google.appengine.api import users
import jinja2
import logging
import datetime
import models

from webapp2_extras import auth
from webapp2_extras import sessions

from webapp2_extras.auth import InvalidAuthIdError
from webapp2_extras.auth import InvalidPasswordError


jinja_environment = jinja2.Environment(autoescape=True,
    loader=jinja2.FileSystemLoader(os.path.join(os.path.dirname(__file__), 'templates')))

def user_required(handler):
    """Decorator that checks if there's a user associated with the current session.
    Will also fail if there's no session present.
    """
    def check_login(self, *args, **kwargs):
        auth = self.auth
        if not auth.get_user_by_session():
            self.redirect(self.render('login.html'), abort=True)
        else:
            return handler(self, *args, **kwargs)

class MyHandler(webapp2.RequestHandler):
    "Setup self.user and self.templateValues values."
    """def setupUser(self):
        self.user = users.get_current_user()
        self.templateValues = {}
        if self.user:
            self.templateValues['logout'] = users.create_logout_url('/')
            self.templateValues['username'] = self.user.nickname()
        else:
            self.templateValues['login'] = users.create_login_url('/')
    """

    def setupUser(self):
        """Returns the implementation of the user model.
           It is consistent with config['webapp2_extras.auth']['user_model'], if set.
        """
        self.user = self.auth.get_user_by_session()
        self.templateValues = {}
        if self.user:
            self.templateValues['logout'] = '/logout'
            self.templateValues['username'] = str(self.user_info['auth_ids'])[3:-2]
            logging.info("username: %s", str(self.user_info['auth_ids'])[3:-2])
        else:
            self.templateValues['login'] = '/login'
            self.templateValues['signup'] = '/signup'

    def render(self, afile):
        "Render the given file"
        template = jinja_environment.get_template(afile)
        self.response.out.write(template.render(self.templateValues))

    def navbarSetup(self):
        self.templateValues['index'] = '/'
        self.templateValues['title'] = 'Class Track'
        self.templateValues['login'] = '/login'
        self.templateValues['signup'] = '/signup'

    @webapp2.cached_property
    def auth(self):
        """Shortcut to access the auth instance as a property."""
        return auth.get_auth()

    @webapp2.cached_property
    def user_info(self):
        """Shortcut to access a subset of the user attributes that are stored
        in the session.

        The list of attributes to store in the session is specified in
          config['webapp2_extras.auth']['user_attributes'].
        :returns
          A dictionary with most user information
        """
        return self.auth.get_user_by_session()

    @webapp2.cached_property
    def user(self):
        """Shortcut to access the current logged in user.

        Unlike user_info, it fetches information from the persistence layer and
        returns an instance of the underlying model.

        :returns
          The instance of the user model associated to the logged in user.
        """
        u = self.user_info
        return self.user_model.get_by_id(u['user_id']) if u else None

    @webapp2.cached_property
    def user_model(self):
        """Returns the implementation of the user model.

        It is consistent with config['webapp2_extras.auth']['user_model'], if set.
        """
        return self.auth.store.user_model

    @webapp2.cached_property
    def session(self):
        """Shortcut to access the current session."""
        return self.session_store.get_session(backend="datastore")

    # this is needed for webapp2 sessions to work
    def dispatch(self):
        # Get a session store for this request.
        self.session_store = sessions.get_store(request=self.request)

        try:
            # Dispatch the request.
            webapp2.RequestHandler.dispatch(self)
        finally:
            # Save all sessions.
            self.session_store.save_sessions(self.response)

    def display_message(self, message):
        """Utility function to display a template with a simple message."""
        self.templateValues = {}
        self.templateValues['message'] = message
        self.render('message.html')

class MainPageHandler(MyHandler):
    def get(self):
        self.setupUser()
        self.navbarSetup()
        if self.user_info:
            self.templateValues['user'] = self.user_info
            self.templateValues['username'] = str(self.user_info['auth_ids'])[3:-2]
            self.templateValues['post'] = '/post'
            self.redirect('/portal/')
        else:
            self.render('index.html')

class NotFoundPageHandler(MyHandler):
    def get(self):
        self.render('404.html')

class PostPageHandler(MyHandler):
    def get(self):
        self.render('post.html')

class SignupPageHandler(MyHandler):
    def get(self):
        self.templateValues = {}
        self.navbarSetup()
        self.templateValues['title'] = "Sign Up"
        self.render('signup.html')

    def post(self):
        password = self.request.get('password')
        email = self.request.get('email')
        first_name = self.request.get('fname')
        last_name = self.request.get('lname')

        user_data = self.user_model.create_user(email,
            first_name=first_name, password_raw=password,
            last_name=last_name, verified=False)
        if not user_data[0]: #user_data is a tuple
            self.display_message('Unable to create user for email %s because of duplicate keys %s' % (email, user_data[1]))
            return

        user = user_data[1]
        user_id = user.get_id()

        token = self.user_model.create_signup_token(user_id)

        verification_url = self.uri_for('verification', type='v', user_id=user_id, signup_token=token, _full=True)

        msg = 'Send an email to user in order to verify their address. They will be able to do so by visiting <a href="{url}">{url}</a>'

        self.display_message(msg.format(url=verification_url))

class VerificationHandler(MyHandler):
    def get(self, *args, **kwargs):
        user = None
        user_id = kwargs['user_id']
        signup_token = kwargs['signup_token']
        verification_type = kwargs['type']
        # it should be something more concise like
        # self.auth.get_user_by_token(user_id, signup_token)
        # unfortunately the auth interface does not (yet) allow to manipulate
        # signup tokens concisely
        user, ts = self.user_model.get_by_auth_token(int(user_id), signup_token,'signup')

        if not user:
            logging.info('Could not find any user with id "%s" signup token "%s"', user_id, signup_token)
            self.abort(404)

        # store user data in the session
        self.auth.set_session(self.auth.store.user_to_dict(user), remember=True)

        if verification_type == 'v':
            # remove signup token, we don't want users to come back with an old link
            self.user_model.delete_signup_token(user.get_id(), signup_token)

            if not user.verified:
                user.verified = True
                user.put()

            self.display_message('User email address has been verified.')
            return

        elif verification_type == 'p':
            # supply user to the page
            params = {
                'user': user,
                'token': signup_token
            }
            self.render('resetpassword.html', params)
        else:
            logging.info('verification type not supported')
            self.abort(404)
class HomePageHandler(MyHandler):
	def get(self):
		self.setupUser()
		class_list = ['Math', 'PE', 'Geography', 'English']
		filter_list = ['School News', 'PTA', 'Grades', 'Assignment', 'Events']
		newsfeed_list = ['LHS went 41-27 against CHS!','Sarah made an 87 on her English-Chapter 5 Test','PTA is holding a meeting on 12/5/14', 'Flu shots will be given 11/19/14','LHS went 41-27 against CHS!','Sarah made an 87 on her English-Chapter 5 Test','PTA is holding a meeting on 12/5/14', 'Flu shots will be given 11/19/14']
		children_list = ['Daniel', 'Maria', 'Lily']
		self.templateValues['user'] = self.user
		self.templateValues['title'] = 'Home'
		self.templateValues['selected_class'] = 'English'
		self.templateValues['classes'] = class_list
		self.templateValues['filter_list'] = filter_list
		self.templateValues['newsfeed_list'] = newsfeed_list
		self.templateValues['children_list'] = children_list
		self.render('home.html')


class SetPasswordHandler(MyHandler):
    @user_required
    def post(self):
        password = self.request.get('password')
        old_token = self.request.get('t')

        if not password or password != self.request.get('confirm_password'):
            self.display_message('passwords do not match')
            return

        user = self.user
        user.set_password(password)
        user.put()

        # remove signup token, we don't want users to come back with an old link
        self.user_model.delete_signup_token(user.get_id(), old_token)

        self.display_message('Password updated')

class ForgotPasswordHandler(MyHandler):
    def get(self):
        self._serve_page()

    def post(self):
        username = self.request.get('user_name')

        user = self.user_model.get_by_auth_id(username)
        if not user:
            logging.info('Could not find any user entry for username %s', username)
            self._serve_page(not_found=True)
            return

        user_id = user.get_id()
        token = self.user_model.create_signup_token(user_id)

        verification_url = self.uri_for('verification', type='p', user_id=user_id, signup_token=token, _full=True)

        msg = 'Send an email to user in order to reset their password. They will be able to do so by visiting <a href="{url}">{url}</a>'

        self.display_message(msg.format(url=verification_url))

    def _serve_page(self, not_found=False):
        username = self.request.get('username')
        self.templateValues['username'] = user_name
        self.templateValues['not_found'] = not_found
        self.render('forgot.html')



class LoginPageHandler(MyHandler):
    def get(self):
        self.templateValues = {}
        self.navbarSetup()
        self.templateValues['title'] = "Login"
        self.render('login.html')

    def post(self):
        email = self.request.get('email') #Get username value from html
        password = self.request.get('password') #Get password value from html
        try:
            u = self.auth.get_user_by_password(email, password, remember=True, save_session=True)
            self.redirect('/')
        except (InvalidAuthIdError, InvalidPasswordError) as e:
            logging.info('Login failed for user %s because of %s', email, type(e))
            self.templateValues = {}
            self.navbarSetup()
            self.templateValues['title'] = "Login"
            self.templateValues['error'] = "Invalid Username or Password."
            self.render('login.html')

class LogoutPageHandler(MyHandler):
    def get(self):
        self.auth.unset_session()
        self.redirect('/')

class AuthenticatedHandler(MyHandler):
    @user_required
    def get(self):
        self.render('authenticated.html')


#Change the model from Card to whatever. I setup a Card model for something else I was doing,
#however the code is still relevant to read over since this is how we'll put posts into
#the datastore.
class PostHandler(MyHandler):
    def get(self):
        self.redirect('/')

    def post(self):
        the_post = self.request.get('the_post')
        owner = str(self.user_info['auth_ids'])

        thePost = models.Post(caption=the_post, owner=owner)

        future = thePost.put()

# Here is an example of how we can use ajax to call one of our handlers. So doing a
# "POST" to the url "/post" runs the post method defined in our PostHandler since
# that is what is set to run when the url "/post" is called. The ajax stuff would obviously
# need to be done in our js file, not here. I'm just lazy and dumping everything into
# the main.
#
#       $("#postButton").click(function(){
#            var caption = $("#thePost").val();
#
#            $.ajax({
#              url: "/post",
#              type: "POST",
#              data: { the_post: caption },
#              success: function() {
#                console.log("yay success!");
#              },
#              error: function(e){
#                console.log(e);
#              }
#            });
#
#        });


class AboutPageHandler(MyHandler):
    def get(self):
        self.setupUser()
        self.navbarSetup()
        if self.user:
            self.templateValues['user'] = self.user
        self.templateValues['title'] = 'ClassTrack'
        self.render('about.html')

class PortalPageHandler(MyHandler):
    def get(self):
        self.setupUser()
        if self.user:
            self.templateValues['user'] = self.user
            self.templateValues['title'] = 'My Portal'
            self.render('portal.html')
        else:
            self.templateValues['title'] = 'ClassTrack'
            self.redirect('/')

class ContactPageHandler(MyHandler):
	def get(self):
		self.setupUser()
		self.navbarSetup()
		self.templateValues['user'] = self.user
		self.templateValues['title'] = 'ClassTrack'
		self.render('contact.html')

class ConferencePageHandler(MyHandler):
	def get(self):
		self.setupUser()
		self.navbarSetup()
		self.templateValues['user'] = self.user
		self.templateValues['title'] = 'WebRTC'
		self.render('chatroom_demo.html')

class NotFoundPageHandler(MyHandler):
	def get(self):
		self.setupUser()
		self.navbarSetup()
		self.templateValues['user'] = self.user
		self.templateValues['title'] = 'ClassTrack'
		self.render('404.html')

config = {
  'webapp2_extras.auth': {
    'user_model': 'models.User',
    'user_attributes': ['auth_ids']
  },
  'webapp2_extras.sessions': {
    'secret_key': 'YOUR_SECRET_KEY'
  }
}

app = webapp2.WSGIApplication([
    webapp2.Route('/', MainPageHandler, name='home'),
    webapp2.Route('/index.html', MainPageHandler, name='index'),
    webapp2.Route('/signup', SignupPageHandler),
    webapp2.Route('/<type:v|p>/<user_id:\d+>-<signup_token:.+>', VerificationHandler, name='verification'),
    webapp2.Route('/password', SetPasswordHandler),
    webapp2.Route('/login', LoginPageHandler, name='login'),
    webapp2.Route('/logout', LogoutPageHandler, name='logout'),
    webapp2.Route('/forgot', ForgotPasswordHandler, name='forgot'),
    webapp2.Route('/authenticated', AuthenticatedHandler, name='authenticated'),
    webapp2.Route('/post', PostHandler, name='post'),
    webapp2.Route('/home.html', HomePageHandler, name='home'),
    webapp2.Route('/portal/', PortalPageHandler, name='portal'),
    webapp2.Route('/about.html', PostHandler, name='about'),
    webapp2.Route('/contact.html', PostHandler, name='contact'),
    webapp2.Route('/.*', NotFoundPageHandler)
], debug=True, config=config)
