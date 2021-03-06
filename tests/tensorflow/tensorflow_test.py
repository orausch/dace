#!/usr/bin/env python

try:
    import tensorflow as tf
except ImportError:
    print("WARNING: Tensorflow not found, skipping test")
    exit(0)

import numpy as np
from dace.frontend.tensorflow import TFSession

if __name__ == '__main__':
    print('DaCe Tensorflow frontend test')

    A = np.random.rand(16, 16).astype(np.float32)
    B = np.random.rand(16, 16).astype(np.float32)

    A_tf = tf.placeholder(tf.float32, shape=[16, 16])
    B_tf = tf.placeholder(tf.float32, shape=[16, 16])

    with TFSession() as sess:
        # Simple matrix multiplication
        C = sess.run(A_tf @ B_tf, feed_dict={A_tf: A, B_tf: B})

    diff = np.linalg.norm(C - (A @ B)) / (16 * 16)
    print("Difference:", diff)
    print("==== Program end ====")
    exit(0 if diff <= 1e-5 else 1)
