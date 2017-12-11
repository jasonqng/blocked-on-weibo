from setuptools import setup, find_packages

setup(name='blockedonweibo',
      version='0.2',
      description='Testing if search keywords are censored on Sina Weibo',
      url='http://github.com/jasonqng/blocked-on-weibo',
      author='Jason Q. Ng',
      author_email='jason.q.ng@gmail.com',
      license='BSD License',
      packages=find_packages(exclude=['docs']),
      zip_safe=False)
